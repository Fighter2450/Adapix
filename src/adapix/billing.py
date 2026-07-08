"""Stripe billing for Adapix.

One plan: $99/month, 14-day free trial, everything included. Checkout is
Stripe-hosted (we never touch card data). The subscription state lives in
a small JSON file at $ADAPIX_VAR/billing.json keyed by org id:

    {"<org_id>": {"customer_id": "...", "subscription_id": "...",
                  "status": "trialing|active|past_due|canceled",
                  "updated_at": 1712345678}}

Raw REST via urllib — the flows used here (create a Checkout Session,
retrieve it, retrieve a subscription) don't justify a dependency.
"""
from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

STRIPE_API = "https://api.stripe.com/v1"


def _key() -> str:
    return os.environ.get("STRIPE_SECRET_KEY", "").strip()


def price_id() -> str:
    return os.environ.get("STRIPE_PRICE_ID", "").strip()


def configured() -> bool:
    return bool(_key() and price_id())


def _call(method: str, path: str, params: dict[str, Any] | None = None) -> dict:
    data = urllib.parse.urlencode(params or {}).encode() if params else None
    req = urllib.request.Request(
        f"{STRIPE_API}{path}",
        data=data,
        headers={"Authorization": f"Bearer {_key()}"},
        method=method,
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


# ---------------------------------------------------------------------------
# Billing state store
# ---------------------------------------------------------------------------
def _store_path() -> Path:
    return Path(os.environ.get("ADAPIX_VAR", ".")) / "billing.json"


def _load() -> dict[str, Any]:
    p = _store_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except Exception:
        return {}


def _save(d: dict[str, Any]) -> None:
    p = _store_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(d, indent=2))


def get_billing(org_id: str) -> dict[str, Any]:
    return _load().get(org_id) or {}


def set_billing(org_id: str, record: dict[str, Any]) -> None:
    d = _load()
    d[org_id] = {**(d.get(org_id) or {}), **record, "updated_at": int(time.time())}
    _save(d)


# ---------------------------------------------------------------------------
# Checkout
# ---------------------------------------------------------------------------
def create_checkout_session(org_id: str, email: str, base_url: str) -> str:
    """Create a subscription Checkout Session and return its hosted URL.

    The 14-day trial lives on the subscription, so the card is saved now
    but nothing is charged until the trial ends.
    """
    params = {
        "mode": "subscription",
        "line_items[0][price]": price_id(),
        "line_items[0][quantity]": "1",
        "subscription_data[trial_period_days]": "14",
        "customer_email": email,
        "client_reference_id": org_id,
        "allow_promotion_codes": "true",
        "success_url": f"{base_url}/app/billing/success?session_id={{CHECKOUT_SESSION_ID}}",
        "cancel_url": f"{base_url}/app/billing",
        "metadata[org_id]": org_id,
        "subscription_data[metadata][org_id]": org_id,
    }
    session = _call("POST", "/checkout/sessions", params)
    return session["url"]


def confirm_checkout(org_id: str, session_id: str) -> dict[str, Any]:
    """Verify a completed Checkout Session server-side and persist the
    subscription for the org. Returns the stored billing record."""
    session = _call("GET", f"/checkout/sessions/{session_id}")
    if session.get("client_reference_id") != org_id:
        raise ValueError("checkout session does not belong to this account")
    sub_id = session.get("subscription")
    status = "unknown"
    if sub_id:
        sub = _call("GET", f"/subscriptions/{sub_id}")
        status = sub.get("status", "unknown")
    set_billing(org_id, {
        "customer_id": session.get("customer"),
        "subscription_id": sub_id,
        "status": status,
    })
    return get_billing(org_id)


def refresh_status(org_id: str) -> str:
    """Re-pull the subscription status from Stripe (cheap poll used by the
    billing page; a webhook can replace this later)."""
    rec = get_billing(org_id)
    sub_id = rec.get("subscription_id")
    if not sub_id or not configured():
        return rec.get("status") or "none"
    try:
        sub = _call("GET", f"/subscriptions/{sub_id}")
        status = sub.get("status", "unknown")
        if status != rec.get("status"):
            set_billing(org_id, {"status": status})
        return status
    except Exception:
        return rec.get("status") or "none"
