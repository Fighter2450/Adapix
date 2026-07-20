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
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

STRIPE_API = "https://api.stripe.com/v1"


def _key() -> str:
    return os.environ.get("STRIPE_SECRET_KEY", "").strip()


def price_id() -> str:
    return os.environ.get("STRIPE_PRICE_ID", "").strip()


def dedicated_line_price_id() -> str:
    return os.environ.get("STRIPE_DEDICATED_LINE_PRICE_ID", "").strip()


def configured() -> bool:
    return bool(_key() and price_id())


def _call(method: str, path: str, params: dict[str, Any] | None = None,
          *, idempotency_key: str | None = None) -> dict:
    data = urllib.parse.urlencode(params or {}).encode() if params else None
    headers = {"Authorization": f"Bearer {_key()}"}
    # Idempotency-Key makes a retried POST (network hiccup, webhook fan-out)
    # reuse Stripe's first result instead of creating a second charge/credit.
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    req = urllib.request.Request(
        f"{STRIPE_API}{path}",
        data=data,
        headers=headers,
        method=method,
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


# ---------------------------------------------------------------------------
# Billing state store
# ---------------------------------------------------------------------------
def _store_path() -> Path:
    return Path(os.environ.get("ADAPIX_VAR", ".")) / "billing.json"


# billing.json is the platform's paid-access gate — a torn write or a
# lost concurrent update stops paying customers. One process-wide lock
# serializes the read-modify-write (Stripe webhook on the event loop +
# billing-page refreshes on the to_thread pool race otherwise), and every
# write is atomic (tmp file + os.replace) so a crash mid-write can never
# truncate it to {} and flip every org to "no card on file".
import threading as _threading

_billing_lock = _threading.RLock()


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
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, indent=2))
    os.replace(tmp, p)


def get_billing(org_id: str) -> dict[str, Any]:
    with _billing_lock:
        return _load().get(org_id) or {}


def set_billing(org_id: str, record: dict[str, Any]) -> None:
    with _billing_lock:
        d = _load()
        d[org_id] = {**(d.get(org_id) or {}), **record, "updated_at": int(time.time())}
        _save(d)


# ---------------------------------------------------------------------------
# Stripe event idempotency — the SAME event id is delivered more than once
# (Stripe retries, and a trial-end fires several subscription.updated events
# in a burst). Processing one twice = a duplicate referral credit. We record
# every handled event id and refuse to process it again.
# ---------------------------------------------------------------------------
_events_lock = _threading.RLock()


def _events_path() -> Path:
    return Path(os.environ.get("ADAPIX_VAR", ".")) / "stripe_events.json"


def mark_stripe_event_processed(event_id: str) -> bool:
    """Record event_id as handled. Returns True if this is the FIRST time
    (caller should process it), False if already seen (caller should skip).
    Keeps the most recent 5000 ids so the file stays small."""
    if not event_id:
        return True
    with _events_lock:
        p = _events_path()
        try:
            seen = json.loads(p.read_text()) if p.exists() else []
        except Exception:
            seen = []
        if event_id in seen:
            return False
        seen.append(event_id)
        if len(seen) > 5000:
            seen = seen[-5000:]
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix(".tmp")
            tmp.write_text(json.dumps(seen))
            os.replace(tmp, p)
        except Exception:
            pass
        return True


# ---------------------------------------------------------------------------
# Checkout
# ---------------------------------------------------------------------------
def create_checkout_session(org_id: str, email: str, base_url: str, trial_days: int = 14) -> str:
    """Create a subscription Checkout Session and return its hosted URL.

    trial_days is the REMAINDER of the org's 14-day app trial — adding a card
    on day 10 gives 4 more free days, not a fresh 14 (no trial stacking).
    """
    params = {
        "mode": "subscription",
        "line_items[0][price]": price_id(),
        "line_items[0][quantity]": "1",
        "customer_email": email,
        "client_reference_id": org_id,
        "allow_promotion_codes": "true",
        "success_url": f"{base_url}/app/billing/success?session_id={{CHECKOUT_SESSION_ID}}",
        "cancel_url": f"{base_url}/app/billing",
        "metadata[org_id]": org_id,
        "subscription_data[metadata][org_id]": org_id,
    }
    if trial_days > 0:
        params["subscription_data[trial_period_days]"] = str(trial_days)
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


def find_subscription_by_org(org_id: str) -> dict | None:
    """Fallback for a missed confirm (closed tab, different login): every
    subscription we create is tagged metadata[org_id], so Stripe itself is
    the source of truth. Records what it finds."""
    try:
        q = urllib.parse.quote(f"metadata['org_id']:'{org_id}'")
        res = _call("GET", f"/subscriptions/search?query={q}&limit=1")
        subs = res.get("data") or []
        if subs:
            sub = subs[0]
            set_billing(org_id, {
                "customer_id": sub.get("customer"),
                "subscription_id": sub.get("id"),
                "status": sub.get("status", "unknown"),
            })
            return sub
    except Exception:
        pass
    return None


def cancel_subscription(org_id: str) -> str:
    """Set the org's subscription to cancel at period end (the honest
    'cancel anytime' — access continues through what's already paid)."""
    rec = get_billing(org_id)
    sub_id = rec.get("subscription_id")
    if not sub_id:
        sub = find_subscription_by_org(org_id)
        sub_id = sub.get("id") if sub else None
    if not sub_id:
        raise ValueError("no subscription on file")
    sub = _call("POST", f"/subscriptions/{sub_id}", {"cancel_at_period_end": "true"})
    status = sub.get("status", "unknown")
    set_billing(org_id, {"status": status, "cancel_at_period_end": True})
    return status


def refresh_status(org_id: str) -> str:
    """Re-pull the subscription status from Stripe (cheap poll used by the
    billing page; a webhook can replace this later)."""
    rec = get_billing(org_id)
    sub_id = rec.get("subscription_id")
    if not sub_id and configured():
        sub = find_subscription_by_org(org_id)
        if sub:
            return sub.get("status", "unknown")
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


def enforce_one_trial_per_card(org_id: str) -> dict:
    """One free trial per CARD, not per email. Stripe fingerprints every
    card (same physical card -> same fingerprint across customers), so a
    second account created just to harvest another free trial or a referral
    reward is caught the moment the card is typed in.

    Policy on a duplicate: the new account keeps working but its trial ends
    NOW (billing starts immediately) and it can never trigger a referral
    reward. A legit owner with two real businesses on one card can still
    pay for both — they just don't get a second free ride.
    """
    rec = get_billing(org_id)
    sub_id = rec.get("subscription_id")
    if not (sub_id and configured()):
        return {"ok": False, "reason": "no subscription"}
    try:
        sub = _call("GET", f"/subscriptions/{sub_id}?expand[]=default_payment_method")
        pm = sub.get("default_payment_method") or {}
        fp = ((pm.get("card") or {}).get("fingerprint")) or ""
        if not fp:
            return {"ok": True, "fingerprint": None}
        # Any OTHER org already on this exact card?
        duplicate_of = None
        for other_id, other in _load().items():
            if other_id != org_id and other.get("card_fingerprint") == fp:
                duplicate_of = other_id
                break
        set_billing(org_id, {"card_fingerprint": fp})
        if duplicate_of and sub.get("status") == "trialing":
            _call("POST", f"/subscriptions/{sub_id}", {"trial_end": "now"})
            set_billing(org_id, {"duplicate_card_of": duplicate_of})
            return {"ok": True, "fingerprint": fp, "duplicate_of": duplicate_of, "trial_ended": True}
        return {"ok": True, "fingerprint": fp, "duplicate_of": duplicate_of}
    except Exception as e:
        return {"ok": False, "reason": str(e)}


def apply_referral_credit(referrer_org_id: str, amount_cents: int = 9900,
                          *, referred_org_id: str | None = None) -> bool:
    """Give the referrer one free month as a Stripe customer-balance credit
    (offsets their next invoice). Returns False when there's no Stripe
    customer to credit yet — caller should leave the reward pending.

    The idempotency key is derived from (referrer, referee) so even if this
    is somehow called twice for the same referral, Stripe applies the credit
    ONCE."""
    rec = get_billing(referrer_org_id)
    customer_id = rec.get("customer_id")
    if not (customer_id and configured()):
        return False
    idem = f"referral-{referrer_org_id}-{referred_org_id or 'x'}"
    try:
        _call("POST", f"/customers/{customer_id}/balance_transactions", {
            "amount": str(-abs(amount_cents)),   # negative = credit
            "currency": "usd",
            "description": "Referral reward — 1 free month (give a month, get a month)",
        }, idempotency_key=idem)
        return True
    except Exception:
        return False


def reconcile_all_billing() -> int:
    """Re-pull every org's real subscription status from Stripe and correct
    any drift in the local cache. This is the safety net for a missed
    webhook (a canceled customer who'd otherwise keep spending on stale
    'active' state forever). Runs hourly. Returns how many records changed."""
    if not configured():
        return 0
    changed = 0
    with _billing_lock:
        orgs = list(_load().items())
    for org_id, rec in orgs:
        sub_id = rec.get("subscription_id")
        if not sub_id:
            continue
        try:
            sub = _call("GET", f"/subscriptions/{sub_id}")
            real = sub.get("status", "unknown")
            if real != rec.get("status"):
                set_billing(org_id, {"status": real})
                changed += 1
        except urllib.error.HTTPError as he:
            # ONLY a genuine 404 (subscription deleted in Stripe) means
            # canceled. A network blip or 5xx must NOT cancel a paying
            # customer — leave the cache untouched and retry next hour.
            if he.code == 404:
                set_billing(org_id, {"status": "canceled"})
                changed += 1
        except Exception:
            pass  # transient — retry next sweep
    return changed


# ---------------------------------------------------------------------------
# Add-ons — extra recurring line items on top of the base subscription (e.g.
# the $1.50/mo dedicated-calling-line upgrade). Each add-on is its own Stripe
# subscription item so it can be removed independently of the base plan.
# ---------------------------------------------------------------------------
def add_subscription_addon(org_id: str, price: str, *, key: str) -> dict:
    """Attach a recurring add-on price to the org's existing subscription.
    Requires an active/trialing subscription already on file — there's
    nothing to attach a paid add-on to otherwise. `key` names the field the
    resulting subscription-item id is stored under (e.g.
    'dedicated_line_item_id'), so it can be found again to remove later."""
    if not price:
        raise ValueError("add-on price not configured")
    rec = get_billing(org_id)
    sub_id = rec.get("subscription_id")
    if not sub_id:
        sub = find_subscription_by_org(org_id)
        sub_id = sub.get("id") if sub else None
    if not sub_id:
        raise ValueError("no active subscription — set up billing first")
    item = _call("POST", "/subscription_items", {
        "subscription": sub_id,
        "price": price,
        "quantity": "1",
    })
    set_billing(org_id, {key: item.get("id")})
    return item


def remove_subscription_addon(org_id: str, *, key: str) -> None:
    """Undo add_subscription_addon — stops the recurring charge. Safe to call
    even if the add-on was never added (no-op)."""
    rec = get_billing(org_id)
    item_id = rec.get(key)
    if not item_id:
        return
    try:
        _call("DELETE", f"/subscription_items/{item_id}")
    except Exception:
        pass  # already removed (e.g. subscription itself was canceled)
    set_billing(org_id, {key: None})


def engine_allowed(org_id: str, org_created_at=None) -> tuple[bool, str]:
    """May the engine spend money (Claude/Twilio/Vapi) for this org?

    - Billing not configured (pre-launch): always yes.
    - Subscription trialing/active: yes (the 14-day trial lives INSIDE the
      Stripe subscription — card required up front, $0 charged until the
      trial ends).
    - Subscription past_due/canceled/unpaid/incomplete: NO — a failed card
      must not keep consuming paid APIs for free.
    - No subscription at all: NO. There is no card-less trial — checkout
      (card on file) is what starts the trial. Changed 7/16 per Rocco.
    """
    if not configured():
        return True, "billing not configured"
    rec = get_billing(org_id)
    status = rec.get("status") or ""
    if status in ("trialing", "active"):
        return True, status
    if status in ("past_due", "canceled", "unpaid", "incomplete", "incomplete_expired"):
        return False, status
    return False, "no card on file — the free trial starts at checkout"
