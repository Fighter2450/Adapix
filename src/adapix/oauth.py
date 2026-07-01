"""OAuth integration for connecting practice email accounts.

Google (Gmail Workspace) and Microsoft 365 supported. Each ORG connects its own
inbox via OAuth (the login itself is the ownership proof) — tokens are stored
per-org in the `email_connections` table, not a shared flat file, so every
business sends follow-ups as themselves.
"""
from __future__ import annotations

import base64
import json
import os
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


def load_tokens(org_id: str) -> dict[str, Any]:
    """Return this org's connection as {provider: {...}} for compatibility
    with the old flat-file shape (at most one provider is ever connected)."""
    import calendar

    from .db import get_session
    from .models import EmailConnection

    with get_session() as s:
        row = s.get(EmailConnection, org_id)
        if row is None:
            return {}
        return {
            row.provider: {
                "email": row.connected_email,
                "name": row.connected_name or "",
                "refresh_token": row.refresh_token or "",
                "access_token": row.access_token or "",
                "expires_at": row.expires_at,
                # connected_at is stored as a naive UTC datetime; use calendar.timegm
                # (not .timestamp(), which assumes local time for naive datetimes).
                "connected_at": calendar.timegm(row.connected_at.utctimetuple()) if row.connected_at else 0,
                "scope": row.scope or "",
            }
        }


def save_tokens(org_id: str, provider: str, data: dict[str, Any]) -> None:
    """Upsert this org's single email connection row."""
    from datetime import datetime as _dt

    from .db import get_session
    from .models import EmailConnection

    with get_session() as s:
        row = s.get(EmailConnection, org_id)
        if row is None:
            row = EmailConnection(org_id=org_id)
            s.add(row)
        row.provider = provider
        row.connected_email = data.get("email", "")
        row.connected_name = data.get("name") or None
        row.refresh_token = data.get("refresh_token") or None
        row.access_token = data.get("access_token") or None
        row.expires_at = int(data.get("expires_at", 0))
        row.scope = data.get("scope") or None
        connected_at = data.get("connected_at")
        if connected_at:
            row.connected_at = _dt.utcfromtimestamp(connected_at)


def get_provider(org_id: str, provider: str) -> dict[str, Any]:
    return load_tokens(org_id).get(provider, {})


def disconnect(org_id: str) -> bool:
    """Remove this org's email connection (whichever provider it is)."""
    from .db import get_session
    from .models import EmailConnection

    with get_session() as s:
        row = s.get(EmailConnection, org_id)
        if row is None:
            return False
        s.delete(row)
        return True


def _states_path() -> Path:
    return Path(os.environ.get("ADAPIX_VAR", ".")) / "oauth_states.json"


def _load_states() -> dict[str, dict[str, Any]]:
    p = _states_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except Exception:
        return {}


def _save_states(states: dict[str, dict[str, Any]]) -> None:
    p = _states_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(states))
    try:
        os.chmod(p, 0o600)
    except Exception:
        pass


def new_state(provider: str) -> str:
    state = secrets.token_urlsafe(32)
    states = _load_states()
    states[state] = {"provider": provider, "created_at": int(time.time())}
    cutoff = int(time.time()) - 600
    for s, meta in list(states.items()):
        if meta.get("created_at", 0) < cutoff:
            del states[s]
    _save_states(states)
    return state


def consume_state(state: str, provider: str) -> bool:
    states = _load_states()
    meta = states.pop(state, None)
    if meta is None:
        return False
    _save_states(states)
    return meta.get("provider") == provider


GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
GOOGLE_SEND_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"
GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/gmail.send",
    "openid",
    "email",
    "profile",
]


def google_auth_url(redirect_uri: str, state: str) -> str:
    from .config import Settings
    s = Settings()
    if not s.google_client_id:
        raise ValueError("GOOGLE_CLIENT_ID not configured in .env")
    params = {
        "client_id": s.google_client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(GOOGLE_SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    return f"{GOOGLE_AUTH_URL}?{urllib.parse.urlencode(params)}"


def _http_post_form(url: str, data: dict[str, str]) -> dict[str, Any]:
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            err_body = e.read().decode()
        except Exception:
            err_body = ""
        raise RuntimeError(f"HTTP {e.code}: {err_body}") from None


def _http_get_json(url: str, *, bearer: str) -> dict[str, Any]:
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {bearer}"})
    try:
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            err_body = e.read().decode()
        except Exception:
            err_body = ""
        raise RuntimeError(f"HTTP {e.code}: {err_body}") from None


def _http_post_json(url: str, body: dict[str, Any], *, bearer: str) -> dict[str, Any]:
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={
            "Authorization": f"Bearer {bearer}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            err_body = e.read().decode()
        except Exception:
            err_body = ""
        raise RuntimeError(f"HTTP {e.code}: {err_body}") from None


def google_exchange_code(code: str, redirect_uri: str) -> dict[str, Any]:
    from .config import Settings
    s = Settings()
    return _http_post_form(GOOGLE_TOKEN_URL, {
        "code": code,
        "client_id": s.google_client_id,
        "client_secret": s.google_client_secret,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    })


def google_refresh(refresh_token: str) -> dict[str, Any]:
    from .config import Settings
    s = Settings()
    return _http_post_form(GOOGLE_TOKEN_URL, {
        "refresh_token": refresh_token,
        "client_id": s.google_client_id,
        "client_secret": s.google_client_secret,
        "grant_type": "refresh_token",
    })


def google_complete_connection(org_id: str, code: str, redirect_uri: str) -> dict[str, Any]:
    tk = google_exchange_code(code, redirect_uri)
    access_token = tk["access_token"]
    info = _http_get_json(GOOGLE_USERINFO_URL, bearer=access_token)
    data = {
        "email": info.get("email", ""),
        "name": info.get("name", ""),
        "refresh_token": tk.get("refresh_token", ""),
        "access_token": access_token,
        "expires_at": int(time.time()) + int(tk.get("expires_in", 3600)),
        "connected_at": int(time.time()),
        "scope": tk.get("scope", ""),
    }
    save_tokens(org_id, "google", data)
    return {"email": data["email"], "name": data["name"]}


def google_access_token(org_id: str) -> str | None:
    g = load_tokens(org_id).get("google")
    if not g or not g.get("refresh_token"):
        return None
    if g.get("access_token") and time.time() < g.get("expires_at", 0) - 60:
        return g["access_token"]
    try:
        new = google_refresh(g["refresh_token"])
    except Exception as e:
        print(f"[oauth] google refresh failed: {e}")
        return None
    g["access_token"] = new["access_token"]
    g["expires_at"] = int(time.time()) + int(new.get("expires_in", 3600))
    save_tokens(org_id, "google", g)
    return g["access_token"]


def google_send(org_id: str, to: str, subject: str, body: str, from_name: str | None = None) -> dict[str, Any]:
    tok = google_access_token(org_id)
    if not tok:
        return {"ok": False, "error": "Google email not connected"}
    g = load_tokens(org_id)["google"]
    practice_email = g["email"]
    practice_name = from_name or g.get("name") or practice_email
    from_header = f"{practice_name} <{practice_email}>"
    raw = "\r\n".join([
        f"From: {from_header}",
        f"To: {to}",
        f"Subject: {subject}",
        "MIME-Version: 1.0",
        "Content-Type: text/plain; charset=UTF-8",
        "",
        body,
    ]).encode()
    encoded = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    try:
        result = _http_post_json(GOOGLE_SEND_URL, {"raw": encoded}, bearer=tok)
        return {"ok": True, "provider_id": result.get("id")}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _ms_authority(tenant_id: str) -> str:
    return f"https://login.microsoftonline.com/{tenant_id}"


MS_SCOPES = ["offline_access", "openid", "email", "profile", "Mail.Send"]


def microsoft_auth_url(redirect_uri: str, state: str) -> str:
    from .config import Settings
    s = Settings()
    if not s.microsoft_client_id:
        raise ValueError("MICROSOFT_CLIENT_ID not configured in .env")
    params = {
        "client_id": s.microsoft_client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(MS_SCOPES),
        "response_mode": "query",
        "state": state,
    }
    return f"{_ms_authority(s.microsoft_tenant_id)}/oauth2/v2.0/authorize?{urllib.parse.urlencode(params)}"


def microsoft_exchange_code(code: str, redirect_uri: str) -> dict[str, Any]:
    from .config import Settings
    s = Settings()
    return _http_post_form(
        f"{_ms_authority(s.microsoft_tenant_id)}/oauth2/v2.0/token",
        {
            "code": code,
            "client_id": s.microsoft_client_id,
            "client_secret": s.microsoft_client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
            "scope": " ".join(MS_SCOPES),
        },
    )


def microsoft_refresh(refresh_token: str) -> dict[str, Any]:
    from .config import Settings
    s = Settings()
    return _http_post_form(
        f"{_ms_authority(s.microsoft_tenant_id)}/oauth2/v2.0/token",
        {
            "refresh_token": refresh_token,
            "client_id": s.microsoft_client_id,
            "client_secret": s.microsoft_client_secret,
            "grant_type": "refresh_token",
            "scope": " ".join(MS_SCOPES),
        },
    )


def microsoft_complete_connection(org_id: str, code: str, redirect_uri: str) -> dict[str, Any]:
    tk = microsoft_exchange_code(code, redirect_uri)
    access_token = tk["access_token"]
    info = _http_get_json("https://graph.microsoft.com/v1.0/me", bearer=access_token)
    data = {
        "email": info.get("mail") or info.get("userPrincipalName") or "",
        "name": info.get("displayName", ""),
        "refresh_token": tk.get("refresh_token", ""),
        "access_token": access_token,
        "expires_at": int(time.time()) + int(tk.get("expires_in", 3600)),
        "connected_at": int(time.time()),
    }
    save_tokens(org_id, "microsoft", data)
    return {"email": data["email"], "name": data["name"]}


def microsoft_access_token(org_id: str) -> str | None:
    m = load_tokens(org_id).get("microsoft")
    if not m or not m.get("refresh_token"):
        return None
    if m.get("access_token") and time.time() < m.get("expires_at", 0) - 60:
        return m["access_token"]
    try:
        new = microsoft_refresh(m["refresh_token"])
    except Exception as e:
        print(f"[oauth] microsoft refresh failed: {e}")
        return None
    m["access_token"] = new["access_token"]
    m["expires_at"] = int(time.time()) + int(new.get("expires_in", 3600))
    if new.get("refresh_token"):
        m["refresh_token"] = new["refresh_token"]
    save_tokens(org_id, "microsoft", m)
    return m["access_token"]


def microsoft_send(org_id: str, to: str, subject: str, body: str, from_name: str | None = None) -> dict[str, Any]:
    tok = microsoft_access_token(org_id)
    if not tok:
        return {"ok": False, "error": "Microsoft email not connected"}
    payload = {
        "message": {
            "subject": subject,
            "body": {"contentType": "Text", "content": body},
            "toRecipients": [{"emailAddress": {"address": to}}],
        },
        "saveToSentItems": True,
    }
    try:
        _http_post_json("https://graph.microsoft.com/v1.0/me/sendMail", payload, bearer=tok)
        return {"ok": True, "provider_id": None}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def send_email(org_id: str, to: str, subject: str, body: str, from_name: str | None = None) -> dict[str, Any]:
    tokens = load_tokens(org_id)
    if tokens.get("google", {}).get("refresh_token"):
        r = google_send(org_id, to, subject, body, from_name=from_name)
        return {**r, "provider": "google"}
    if tokens.get("microsoft", {}).get("refresh_token"):
        r = microsoft_send(org_id, to, subject, body, from_name=from_name)
        return {**r, "provider": "microsoft"}
    return {"ok": False, "error": "no email provider connected", "provider": None}


def status(org_id: str) -> dict[str, Any]:
    """This org's email connection status — used by the Settings UI. At most
    one provider is connected at a time (connecting one replaces the other)."""
    tokens = load_tokens(org_id)
    out = {}
    for prov in ("google", "microsoft"):
        t = tokens.get(prov, {})
        if t.get("refresh_token"):
            out[prov] = {
                "connected": True,
                "email": t.get("email", ""),
                "name": t.get("name", ""),
                "connected_at": t.get("connected_at", 0),
                "scope": t.get("scope", ""),
            }
        else:
            out[prov] = {"connected": False}
    return out


def is_connected(org_id: str) -> bool:
    tokens = load_tokens(org_id)
    return bool(tokens.get("google", {}).get("refresh_token") or tokens.get("microsoft", {}).get("refresh_token"))
