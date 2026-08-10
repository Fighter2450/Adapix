"""Salesforce connector — pipeline sync + activity write-back.

The honest version of "Salesforce integration":

  * OAuth2 (authorization-code + refresh token) against login.salesforce.com,
    configured by SALESFORCE_CLIENT_ID / SALESFORCE_CLIENT_SECRET from a
    Connected App (see docs/SALESFORCE_SETUP.md). Inert until those are set.
  * Sync pulls OPEN Opportunities and their primary contact into Adapix:
    an Opportunity is a quote (name -> treatment_type, Amount -> value),
    so a stalled one becomes exactly the "quiet quote" the engine chases.
    Closed Won -> treatment_started, Closed Lost -> explicitly_declined.
  * Every follow-up Adapix actually sends is logged back to Salesforce as a
    completed Task on the Opportunity, so the CRM stays the system of record.

Token storage: OrgProfile.data["salesforce"] (no new tables). Mapping:
Patient.external_id = "sf:<OpportunityId>:<ContactId>" (fits String(128)).
"""
from __future__ import annotations

import threading
import urllib.parse
import urllib.request
import json
import logging
from datetime import datetime, date
from typing import Any

log = logging.getLogger("adapix.salesforce")

AUTH_BASE = "https://login.salesforce.com/services/oauth2/authorize"
TOKEN_URL = "https://login.salesforce.com/services/oauth2/token"
API_VERSION = "v59.0"
SCOPES = "api refresh_token"

# Salesforce sends webhooks/nothing here — we poll. Keep it gentle.
SYNC_INTERVAL_SECONDS = 3600


# ---------------------------------------------------------------------------
# Token storage (OrgProfile.data["salesforce"])
# ---------------------------------------------------------------------------

def _load_sf(org_id: str) -> dict[str, Any]:
    from .db import get_session
    from .models import OrgProfile
    with get_session() as s:
        prof = s.query(OrgProfile).filter(OrgProfile.org_id == org_id).first()
        return dict((prof.data or {}).get("salesforce") or {}) if prof else {}


def _save_sf(org_id: str, data: dict[str, Any] | None) -> None:
    from .db import get_session
    from .models import OrgProfile
    with get_session() as s:
        prof = s.query(OrgProfile).filter(OrgProfile.org_id == org_id).first()
        if prof is None:
            prof = OrgProfile(org_id=org_id, data={})
            s.add(prof)
        d = dict(prof.data or {})
        if data is None:
            d.pop("salesforce", None)
        else:
            d["salesforce"] = data
        prof.data = d


def status(org_id: str) -> dict[str, Any]:
    sf = _load_sf(org_id)
    return {
        "connected": bool(sf.get("refresh_token")),
        "instance_url": sf.get("instance_url"),
        "identity": sf.get("identity"),
        "last_sync": sf.get("last_sync"),
        "last_sync_result": sf.get("last_sync_result"),
        "configured": _configured(),
    }


def _configured() -> bool:
    from .config import Settings
    s = Settings()
    return bool(s.salesforce_client_id and s.salesforce_client_secret)


def disconnect(org_id: str) -> bool:
    if not _load_sf(org_id):
        return False
    _save_sf(org_id, None)
    return True


# ---------------------------------------------------------------------------
# OAuth
# ---------------------------------------------------------------------------

def auth_url(redirect_uri: str, state: str) -> str:
    from .config import Settings
    s = Settings()
    if not s.salesforce_client_id:
        raise ValueError("SALESFORCE_CLIENT_ID not configured — see docs/SALESFORCE_SETUP.md")
    params = {
        "client_id": s.salesforce_client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": SCOPES,
        "state": state,
        "prompt": "login consent",
    }
    return f"{AUTH_BASE}?{urllib.parse.urlencode(params)}"


def _post_form(url: str, data: dict[str, str]) -> dict[str, Any]:
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def exchange_code(code: str, redirect_uri: str) -> dict[str, Any]:
    from .config import Settings
    s = Settings()
    return _post_form(TOKEN_URL, {
        "grant_type": "authorization_code",
        "code": code,
        "client_id": s.salesforce_client_id,
        "client_secret": s.salesforce_client_secret,
        "redirect_uri": redirect_uri,
    })


def _refresh(refresh_token: str) -> dict[str, Any]:
    from .config import Settings
    s = Settings()
    return _post_form(TOKEN_URL, {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": s.salesforce_client_id,
        "client_secret": s.salesforce_client_secret,
    })


def complete_connection(org_id: str, code: str, redirect_uri: str) -> dict[str, Any]:
    tok = exchange_code(code, redirect_uri)
    if "refresh_token" not in tok or "instance_url" not in tok:
        raise RuntimeError(f"Salesforce token exchange incomplete: {list(tok.keys())}")
    identity = None
    try:
        # id field is a URL identifying the user; fetch display info best-effort
        idj = _get_json(tok["id"], tok["access_token"])
        identity = idj.get("username") or idj.get("display_name")
    except Exception:
        pass
    _save_sf(org_id, {
        "refresh_token": tok["refresh_token"],
        "access_token": tok["access_token"],
        "instance_url": tok["instance_url"],
        "identity": identity,
        "connected_at": datetime.utcnow().isoformat() + "Z",
    })
    # First sync right away, off-thread so the OAuth redirect returns fast.
    threading.Thread(target=sync_org, args=(org_id,), daemon=True).start()
    return {"connected": True, "identity": identity}


def _access(org_id: str) -> tuple[str, str] | None:
    """(access_token, instance_url), refreshing if needed. None if not connected."""
    sf = _load_sf(org_id)
    if not sf.get("refresh_token"):
        return None
    return sf["access_token"], sf["instance_url"]


def _access_fresh(org_id: str) -> tuple[str, str] | None:
    sf = _load_sf(org_id)
    if not sf.get("refresh_token"):
        return None
    tok = _refresh(sf["refresh_token"])
    sf["access_token"] = tok["access_token"]
    sf["instance_url"] = tok.get("instance_url", sf["instance_url"])
    _save_sf(org_id, sf)
    return sf["access_token"], sf["instance_url"]


def _get_json(url: str, bearer: str) -> dict[str, Any]:
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {bearer}"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def _api(org_id: str, path: str, *, method: str = "GET", body: dict | None = None) -> dict[str, Any]:
    """Call the REST API with auto-refresh-on-401."""
    acc = _access(org_id)
    if acc is None:
        raise RuntimeError("Salesforce not connected")
    for attempt in (0, 1):
        token, instance = acc
        url = instance.rstrip("/") + path
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        })
        try:
            with urllib.request.urlopen(req, timeout=45) as r:
                raw = r.read().decode()
                return json.loads(raw) if raw.strip() else {}
        except urllib.error.HTTPError as e:
            if e.code == 401 and attempt == 0:
                acc = _access_fresh(org_id)
                if acc is None:
                    raise
                continue
            detail = e.read().decode()[:300]
            raise RuntimeError(f"Salesforce API {e.code}: {detail}") from e
    raise RuntimeError("unreachable")


def _soql(org_id: str, query: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    path = f"/services/data/{API_VERSION}/query?q={urllib.parse.quote(query)}"
    while path:
        j = _api(org_id, path)
        out.extend(j.get("records", []))
        path = j.get("nextRecordsUrl") or ""
    return out


# ---------------------------------------------------------------------------
# Sync: Opportunities -> Adapix contacts/quotes
# ---------------------------------------------------------------------------

_OPEN_STATUS = "consulted_not_started"   # legacy naming: an open quote


def sync_org(org_id: str) -> dict[str, Any]:
    """Pull Opportunities (open + recently closed) with their primary contact
    and upsert them as Adapix contacts. Returns a summary dict; never raises
    (records the error in status instead)."""
    from .db import get_session
    from .models import Patient
    result = {"pulled": 0, "created": 0, "updated": 0, "skipped_no_contact": 0, "error": None}
    try:
        rows = _soql(org_id, (
            "SELECT Id, Name, Amount, StageName, IsClosed, IsWon, CloseDate, CreatedDate, "
            " (SELECT ContactId, Contact.FirstName, Contact.LastName, Contact.Phone, "
            "  Contact.MobilePhone, Contact.Email FROM OpportunityContactRoles "
            "  ORDER BY IsPrimary DESC LIMIT 1) "
            "FROM Opportunity "
            "WHERE IsClosed = false OR CloseDate = LAST_N_DAYS:30 "
            "ORDER BY CreatedDate DESC LIMIT 500"
        ))
        result["pulled"] = len(rows)
        with get_session() as s:
            for opp in rows:
                roles = ((opp.get("OpportunityContactRoles") or {}).get("records")) or []
                if not roles:
                    result["skipped_no_contact"] += 1
                    continue
                role = roles[0]
                contact = role.get("Contact") or {}
                ext = f"sf:{opp['Id']}:{role.get('ContactId','')}"[:128]
                phone = contact.get("MobilePhone") or contact.get("Phone") or None
                if phone:
                    from .phone import normalize_phone
                    phone = normalize_phone(phone) or phone
                if opp.get("IsClosed"):
                    status_val = "treatment_started" if opp.get("IsWon") else "explicitly_declined"
                else:
                    status_val = _OPEN_STATUS
                p = s.query(Patient).filter(
                    Patient.practice_id == org_id, Patient.external_id == ext).first()
                if p is None:
                    # match by phone/email so re-imports don't duplicate people
                    if phone:
                        p = s.query(Patient).filter(
                            Patient.practice_id == org_id, Patient.phone == phone).first()
                    if p is None and contact.get("Email"):
                        p = s.query(Patient).filter(
                            Patient.practice_id == org_id, Patient.email == contact["Email"]).first()
                created = False
                if p is None:
                    p = Patient(practice_id=org_id)
                    s.add(p)
                    created = True
                p.external_id = ext
                p.first_name = contact.get("FirstName") or p.first_name or ""
                p.last_name = contact.get("LastName") or p.last_name or ""
                if phone:
                    p.phone = phone
                if contact.get("Email"):
                    p.email = contact["Email"]
                p.treatment_type = (opp.get("Name") or "")[:200] or p.treatment_type
                if opp.get("Amount") is not None:
                    p.treatment_plan_amount = opp["Amount"]
                # don't resurrect someone the owner manually declined/paused
                if created or p.status in (_OPEN_STATUS, "treatment_started", "explicitly_declined"):
                    p.status = status_val
                if created:
                    cd = (opp.get("CreatedDate") or "")[:10]
                    try:
                        p.consult_date = date.fromisoformat(cd)
                    except ValueError:
                        pass
                    result["created"] += 1
                else:
                    result["updated"] += 1
    except Exception as e:
        result["error"] = str(e)[:300]
        log.warning("Salesforce sync failed for %s: %s", org_id, e)
    sf = _load_sf(org_id)
    if sf:
        sf["last_sync"] = datetime.utcnow().isoformat() + "Z"
        sf["last_sync_result"] = result
        _save_sf(org_id, sf)
    return result


def sync_all_connected() -> None:
    """Hourly background pass: sync every org with a Salesforce connection."""
    from .db import get_session
    from .models import OrgProfile
    if not _configured():
        return
    with get_session() as s:
        org_ids = [p.org_id for p in s.query(OrgProfile).all()
                   if (p.data or {}).get("salesforce", {}).get("refresh_token")]
    for oid in org_ids:
        sync_org(oid)


# ---------------------------------------------------------------------------
# Write-back: sent follow-up -> completed Task on the Opportunity
# ---------------------------------------------------------------------------

def write_back_sent(org_id: str, patient_external_id: str | None,
                    channel: str, body_text: str) -> None:
    """Best-effort, fire-and-forget from a thread. Never raises."""
    try:
        if not (patient_external_id or "").startswith("sf:"):
            return
        parts = patient_external_id.split(":")
        opp_id = parts[1] if len(parts) > 1 else ""
        contact_id = parts[2] if len(parts) > 2 else ""
        if not opp_id:
            return
        label = {"sms": "Text", "email": "Email", "call": "Call"}.get(channel, channel)
        task = {
            "Subject": f"Adapix follow-up ({label}) sent",
            "Description": (body_text or "")[:2000],
            "Status": "Completed",
            "ActivityDate": date.today().isoformat(),
            "WhatId": opp_id,
        }
        if contact_id:
            task["WhoId"] = contact_id
        _api(org_id, f"/services/data/{API_VERSION}/sobjects/Task", method="POST", body=task)
    except Exception as e:
        log.warning("Salesforce write-back failed for %s: %s", org_id, e)


def write_back_async(org_id: str | None, patient_external_id: str | None,
                     channel: str, body_text: str) -> None:
    if not org_id or not (patient_external_id or "").startswith("sf:"):
        return
    threading.Thread(target=write_back_sent,
                     args=(org_id, patient_external_id, channel, body_text),
                     daemon=True).start()
