"""Web Push notifications for the Adapix PWA.

This is the plumbing that lets the bot push real notifications to the
practice owner's phone — lock-screen alerts, badges, etc — without
needing Twilio, an SMS line, or any phone-number routing. It's pure
Web Push API, the same mechanism Gmail / Slack / Twitter use to push
to your browser.

How it works:

  1) The server has a VAPID keypair. Public key shipped to the PWA;
     private key kept here and used to sign push payloads. We
     auto-generate the keypair on first run and persist it.

  2) On the user's phone, the PWA service worker subscribes to push
     via the browser's push manager. The browser hands the service
     worker a "subscription" object containing an endpoint URL
     (Apple/Google/Mozilla push gateway) + two crypto keys.

  3) The PWA POSTs that subscription back to us, we store it.

  4) Any time something interesting happens (chatbot learns a fact,
     escalation fires, etc), we call `push_notification(...)` which
     uses pywebpush to send to every stored subscription.

Persistence is a single JSON file at $ADAPIX_VAR/subscriptions.json.
For v0 there's no per-user routing — every subscription gets every
notification. We'll add per-user filtering when there's more than one
practice device.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

# pywebpush + py_vapid are optional — if they aren't installed, the
# rest of the app still boots; notification endpoints will just return
# "not configured" errors.
try:
    from pywebpush import webpush, WebPushException
    from py_vapid import Vapid
    _PUSH_AVAILABLE = True
except Exception:
    webpush = None  # type: ignore
    WebPushException = Exception  # type: ignore
    Vapid = None  # type: ignore
    _PUSH_AVAILABLE = False


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
def _var_dir() -> Path:
    return Path(os.environ.get("ADAPIX_VAR", "."))


def _vapid_path() -> Path:
    return _var_dir() / "vapid_keys.json"


def _subs_path() -> Path:
    return _var_dir() / "subscriptions.json"


# ---------------------------------------------------------------------------
# VAPID keypair (auto-generated on first request, persisted thereafter)
# ---------------------------------------------------------------------------
def get_vapid_keys() -> dict[str, str]:
    """Return {'public_key': ..., 'private_key': ..., 'subject': ...}.

    The public key gets sent to the PWA. The private key is used here to
    sign push payloads. If no keypair exists yet we generate one.
    """
    # Env-var keys win: on ephemeral hosts (Railway) the data dir is wiped
    # every deploy, which would rotate the keypair and orphan every push
    # subscription. Set VAPID_PRIVATE_KEY / VAPID_PUBLIC_KEY in the host env.
    env_priv = os.environ.get("VAPID_PRIVATE_KEY", "").strip()
    env_pub = os.environ.get("VAPID_PUBLIC_KEY", "").strip()
    if env_priv and env_pub:
        return {
            "public_key": env_pub,
            "private_key": env_priv,
            "subject": os.environ.get("ADAPIX_VAPID_SUBJECT", "mailto:hello@adapixai.com"),
        }

    p = _vapid_path()
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:
            pass  # corrupt — regenerate below

    if not _PUSH_AVAILABLE:
        # Return empty placeholders so the API can return a sensible 503.
        return {"public_key": "", "private_key": "", "subject": ""}

    # Generate a fresh ES256 keypair via py_vapid. The Vapid01 class
    # generates private + public keys in raw / PEM form depending on
    # what we ask for.
    v = Vapid()
    v.generate_keys()

    # py_vapid public_key.public_bytes(...) returns the uncompressed
    # SEC1 point (65 bytes starting with 0x04). pywebpush expects the
    # base64-urlsafe encoding of that.
    import base64
    from cryptography.hazmat.primitives import serialization

    pub_raw = v.public_key.public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint,
    )
    pub_b64 = base64.urlsafe_b64encode(pub_raw).rstrip(b"=").decode("ascii")

    priv_pem = v.private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")

    keys = {
        "public_key": pub_b64,
        "private_key": priv_pem,
        # mailto: subject is required by the spec — push services may
        # contact this address if they detect abuse. Use a sane default;
        # users can override via ADAPIX_VAPID_SUBJECT env var.
        "subject": os.environ.get("ADAPIX_VAPID_SUBJECT", "mailto:hello@adapixai.com"),
    }
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(keys, indent=2))
    return keys


# ---------------------------------------------------------------------------
# Subscription storage
# ---------------------------------------------------------------------------
def _load_subs() -> list[dict[str, Any]]:
    p = _subs_path()
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text())
    except Exception:
        return []


def _save_subs(subs: list[dict[str, Any]]) -> None:
    p = _subs_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(subs, indent=2))


def add_subscription(sub: dict[str, Any], org_id: str | None = None) -> dict[str, Any]:
    """Persist a browser subscription object. De-duplicates by endpoint
    so the same device subscribing twice doesn't get notified twice.
    org_id ties the device to a tenant so one business's notifications
    never buzz another business's phone."""
    endpoint = (sub or {}).get("endpoint")
    if not endpoint:
        raise ValueError("subscription missing 'endpoint'")
    subs = _load_subs()
    subs = [s for s in subs if s.get("endpoint") != endpoint]
    record = {
        "endpoint": endpoint,
        "keys": (sub.get("keys") or {}),
        "user_agent": sub.get("user_agent") or "",
        "org_id": org_id,
        "added_at": int(time.time()),
    }
    subs.append(record)
    _save_subs(subs)
    return record


def remove_subscription(endpoint: str) -> bool:
    subs = _load_subs()
    new_subs = [s for s in subs if s.get("endpoint") != endpoint]
    if len(new_subs) == len(subs):
        return False
    _save_subs(new_subs)
    return True


def list_subscriptions(org_id: str | None = None) -> list[dict[str, Any]]:
    subs = _load_subs()
    if org_id is not None:
        subs = [s for s in subs if s.get("org_id") == org_id]
    return subs


# ---------------------------------------------------------------------------
# Push sending
# ---------------------------------------------------------------------------
def push_notification(
    title: str,
    body: str,
    *,
    url: str | None = None,
    tag: str | None = None,
    org_id: str | None = None,
) -> dict[str, Any]:
    """Send a Web Push notification to every registered subscription.

    Returns a small stats dict — useful for logging and for the /test
    endpoint to report back to the UI ('2 of 2 delivered').

    Failed / stale subscriptions (HTTP 404 or 410 from the push gateway)
    are auto-removed so they don't keep failing forever.
    """
    if not _PUSH_AVAILABLE:
        return {"ok": False, "error": "pywebpush not installed", "sent": 0, "failed": 0}

    keys = get_vapid_keys()
    if not keys.get("private_key"):
        return {"ok": False, "error": "VAPID keys not configured", "sent": 0, "failed": 0}

    subs = _load_subs()
    if org_id is not None:
        # Tenant-scoped push: only this org's devices. Subscriptions from
        # before org tagging (org_id missing) are never matched here.
        subs = [s for s in subs if s.get("org_id") == org_id]
    if not subs:
        return {"ok": False, "error": "no subscriptions", "sent": 0, "failed": 0}

    payload = json.dumps({
        "title": title,
        "body": body,
        "url": url or "/app",
        "tag": tag or "adapix",
    })

    sent = 0
    failed = 0
    to_drop: list[str] = []
    for sub in subs:
        try:
            webpush(
                subscription_info={
                    "endpoint": sub["endpoint"],
                    "keys": sub.get("keys") or {},
                },
                data=payload,
                vapid_private_key=keys["private_key"],
                vapid_claims={"sub": keys["subject"]},
            )
            sent += 1
        except WebPushException as e:
            failed += 1
            status = getattr(getattr(e, "response", None), "status_code", None)
            # 404 / 410 mean the subscription is gone — drop it so we
            # don't keep trying.
            if status in (404, 410):
                to_drop.append(sub["endpoint"])
        except Exception:
            failed += 1

    if to_drop:
        remaining = [s for s in subs if s["endpoint"] not in to_drop]
        _save_subs(remaining)

    return {"ok": sent > 0, "sent": sent, "failed": failed, "dropped": len(to_drop)}
