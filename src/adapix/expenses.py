"""Expense tracker — founder bookkeeping for the cost of building Adapix.

Persists every expense to a single JSON file in $ADAPIX_VAR/expenses.json.
Includes a Claude-powered extractor that detects expense info inside
arbitrary chat messages (so you can drop "$52 1TB SSD from Amazon" into
the chatbot and have it logged automatically).

Categories are tuned for the kinds of things Rocco actually spends on
while building this product. Add more freely — they're just suggestions
for the UI; the storage layer accepts any string.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

# Categories shown as autocomplete suggestions in the UI. Storage accepts
# any string, so users can type a new one and it'll work and start
# appearing in the suggestion list immediately. Use `all_categories()`
# to get the deduplicated combined list (defaults + user-added).
DEFAULT_CATEGORIES = [
    "Hardware",
    "API costs",
    "Domain / hosting",
    "Software / services",
    "Materials",
    "Tools",
    "Office / equipment",
    "Legal / accounting",
    "Inventory",
    "Shipping",
    "Marketing",
    "Phone / internet",
    "Travel",
    "Food / meals",
    "Education",
    "Insurance",
    "Banking fees",
    "Salaries / contractors",
    "Refunds",
    "Other",
]


def all_categories() -> list[str]:
    """Return DEFAULT_CATEGORIES plus any categories the user has typed
    on past expenses or subscriptions, deduplicated and sorted with
    the defaults first."""
    seen = set()
    out: list[str] = []
    for c in DEFAULT_CATEGORIES:
        if c not in seen:
            seen.add(c); out.append(c)
    custom: set[str] = set()
    for it in _load():
        c = (it.get("category") or "").strip()
        if c and c not in seen:
            custom.add(c)
    for s in _load_subs():
        c = (s.get("category") or "").strip()
        if c and c not in seen:
            custom.add(c)
    for c in sorted(custom):
        out.append(c)
    return out


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------
def _var_dir() -> Path:
    return Path(os.environ.get("ADAPIX_VAR", "."))


def _path() -> Path:
    return _var_dir() / "expenses.json"


def _load() -> list[dict[str, Any]]:
    p = _path()
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text())
    except Exception:
        return []


def _save(items: list[dict[str, Any]]) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(items, indent=2))


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------
def add_expense(
    amount: float,
    category: str,
    vendor: str = "",
    description: str = "",
    *,
    date: str | None = None,    # ISO YYYY-MM-DD; defaults to today UTC
    source: str = "manual",     # "manual" or "chat" — provenance hint
) -> dict[str, Any]:
    """Add an expense. Returns the persisted record (with id)."""
    items = _load()
    record = {
        "id":          uuid.uuid4().hex[:12],
        "amount":      round(float(amount), 2),
        "category":    (category or "Other").strip(),
        "vendor":      (vendor or "").strip(),
        "description": (description or "").strip(),
        "date":        date or time.strftime("%Y-%m-%d", time.gmtime()),
        "created_at":  int(time.time()),
        "source":      source,
    }
    items.append(record)
    _save(items)
    return record


def remove_expense(expense_id: str) -> bool:
    items = _load()
    new_items = [it for it in items if it.get("id") != expense_id]
    if len(new_items) == len(items):
        return False
    _save(new_items)
    return True


def list_expenses() -> list[dict[str, Any]]:
    """Return all expenses, newest first."""
    items = _load()
    return sorted(items, key=lambda r: (r.get("date", ""), r.get("created_at", 0)), reverse=True)


# ---------------------------------------------------------------------------
# Aggregations
# ---------------------------------------------------------------------------
def totals_by_category() -> dict[str, float]:
    out: dict[str, float] = {}
    for it in _load():
        cat = it.get("category", "Other") or "Other"
        out[cat] = round(out.get(cat, 0.0) + float(it.get("amount", 0)), 2)
    return dict(sorted(out.items(), key=lambda kv: kv[1], reverse=True))


def totals_by_month() -> dict[str, float]:
    """Return {'2026-05': 412.50, '2026-04': 89.99, ...}, newest first."""
    out: dict[str, float] = {}
    for it in _load():
        d = it.get("date", "")
        if len(d) >= 7:
            month = d[:7]
            out[month] = round(out.get(month, 0.0) + float(it.get("amount", 0)), 2)
    return dict(sorted(out.items(), reverse=True))


def total_all_time() -> float:
    return round(sum(float(it.get("amount", 0)) for it in _load()), 2)


def total_this_month() -> float:
    month = time.strftime("%Y-%m", time.gmtime())
    return totals_by_month().get(month, 0.0)


def count_all() -> int:
    return len(_load())


# ---------------------------------------------------------------------------
# CSV export
# ---------------------------------------------------------------------------
def to_csv() -> str:
    """Return a CSV string of every expense, sorted by date."""
    items = sorted(_load(), key=lambda r: r.get("date", ""))
    lines = ["date,amount,category,vendor,description,source,id"]
    for it in items:
        def esc(s: str) -> str:
            s = str(s or "").replace('"', '""')
            return f'"{s}"' if "," in s or '"' in s else s
        lines.append(",".join([
            esc(it.get("date", "")),
            f'{float(it.get("amount", 0)):.2f}',
            esc(it.get("category", "")),
            esc(it.get("vendor", "")),
            esc(it.get("description", "")),
            esc(it.get("source", "")),
            esc(it.get("id", "")),
        ]))
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Claude-powered expense extraction from natural language
# ---------------------------------------------------------------------------
EXTRACTION_SYSTEM = """\
You're an expense parser. Given an arbitrary user message, decide whether
it describes a purchase / expense that should be logged, and if so extract
the structured fields.

Output STRICT JSON only, no markdown, no prose:
{"is_expense": <bool>, "amount": <float>, "category": <str>, "vendor": <str>, "description": <str>}

Rules:
- "is_expense" is true ONLY if the message clearly describes money spent
  (e.g. "spent $52 on an SSD", "bought a Pi 5 for $80", "$30 PoE HAT from
  Amazon", "paid Anthropic $40 for credits"). Don't fire on questions
  about prices, hypotheticals, or future intent.
- "amount" is the dollar amount as a positive float. If the user wrote
  "$1,234.56" return 1234.56. If no amount is present and you still feel
  it's an expense, return 0.
- "category" is your best guess from this set: Hardware, API costs,
  Domain / hosting, Software / services, Materials, Office / equipment,
  Marketing, Travel, Other. Use Hardware for any physical electronic
  component or device (Pi, SSD, HAT, sensor, screen, etc).
- "vendor" is who they bought it from if mentioned ("Amazon", "Anthropic",
  "Namecheap"); empty string if not specified.
- "description" is a short human-readable label (≤ 60 chars) like
  "1TB NVMe SSD" or "Anthropic API credits".

If is_expense is false, return the rest of the fields as empty defaults
(0, "", "", "").
"""


def extract_expense_from_message(message: str) -> dict[str, Any] | None:
    """Run Claude on `message` and return a parsed expense dict if it
    looks like an expense, else None. Does NOT persist — callers should
    call add_expense(...) with the returned fields.
    """
    if not message or not message.strip():
        return None
    try:
        from anthropic import Anthropic
        from .config import Settings
        settings = Settings()
        client = Anthropic(api_key=settings.anthropic_api_key)
        resp = client.messages.create(
            model=settings.adapix_model,
            max_tokens=200,
            system=EXTRACTION_SYSTEM,
            messages=[{"role": "user", "content": message}],
        )
        text = "".join(b.text for b in resp.content if hasattr(b, "text")).strip()
        # Strip any stray markdown fencing
        if text.startswith("```"):
            text = text.split("```", 2)[-1].strip()
            if text.startswith("json\n"):
                text = text[5:]
        parsed = json.loads(text)
        if not parsed.get("is_expense"):
            return None
        return {
            "amount":      float(parsed.get("amount", 0) or 0),
            "category":    str(parsed.get("category", "Other") or "Other"),
            "vendor":      str(parsed.get("vendor", "") or ""),
            "description": str(parsed.get("description", "") or ""),
        }
    except Exception as e:
        print(f"[expenses] extraction failed: {e}")
        return None


def remember_expense_from_message(message: str) -> dict[str, Any] | None:
    """Extract + persist in one call. Returns the saved record or None."""
    parsed = extract_expense_from_message(message)
    if not parsed:
        return None
    return add_expense(
        amount=parsed["amount"],
        category=parsed["category"],
        vendor=parsed["vendor"],
        description=parsed["description"],
        source="chat",
    )


# ===========================================================================
# Subscriptions — recurring (monthly / yearly) expenses that auto-charge.
# ===========================================================================
def _subs_path() -> Path:
    return _var_dir() / "subscriptions.json"


def _load_subs() -> list[dict[str, Any]]:
    p = _subs_path()
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text())
    except Exception:
        return []


def _save_subs(items: list[dict[str, Any]]) -> None:
    p = _subs_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(items, indent=2))


def _advance(date_iso: str, cycle: str) -> str:
    """Advance an ISO date string by one billing cycle."""
    y, m, d = (int(x) for x in date_iso.split("-"))
    if cycle == "yearly":
        y += 1
    else:
        m += 1
        if m > 12:
            m -= 12
            y += 1
    import calendar
    last_day = calendar.monthrange(y, m)[1]
    d = min(d, last_day)
    return f"{y:04d}-{m:02d}-{d:02d}"


def add_subscription(
    amount: float,
    cycle: str,
    vendor: str = "",
    description: str = "",
    *,
    category: str = "Software / services",
    start_date: str | None = None,
) -> dict[str, Any]:
    cycle = cycle if cycle in ("monthly", "yearly") else "monthly"
    today = time.strftime("%Y-%m-%d", time.gmtime())
    sd = start_date or today
    sub = {
        "id":          uuid.uuid4().hex[:12],
        "amount":      round(float(amount), 2),
        "cycle":       cycle,
        "vendor":      (vendor or "").strip(),
        "description": (description or "").strip(),
        "category":    (category or "Software / services").strip(),
        "start_date":  sd,
        "next_charge": sd,
        "active":      True,
        "created_at":  int(time.time()),
    }
    subs = _load_subs()
    subs.append(sub)
    _save_subs(subs)
    process_due_subscriptions()
    return sub


def cancel_subscription(sub_id: str) -> bool:
    subs = _load_subs()
    found = False
    for s in subs:
        if s.get("id") == sub_id and s.get("active"):
            s["active"] = False
            s["cancelled_at"] = int(time.time())
            found = True
    if found:
        _save_subs(subs)
    return found


def delete_subscription(sub_id: str) -> bool:
    subs = _load_subs()
    new_subs = [s for s in subs if s.get("id") != sub_id]
    if len(new_subs) == len(subs):
        return False
    _save_subs(new_subs)
    return True


def list_subscriptions() -> list[dict[str, Any]]:
    return _load_subs()


def monthly_burn() -> float:
    total = 0.0
    for s in _load_subs():
        if not s.get("active"):
            continue
        amt = float(s.get("amount", 0))
        if s.get("cycle") == "yearly":
            total += amt / 12.0
        else:
            total += amt
    return round(total, 2)


def yearly_burn() -> float:
    return round(monthly_burn() * 12, 2)


def process_due_subscriptions() -> int:
    subs = _load_subs()
    today = time.strftime("%Y-%m-%d", time.gmtime())
    added = 0
    changed = False
    for s in subs:
        if not s.get("active"):
            continue
        while s.get("next_charge", today) <= today:
            charge_date = s["next_charge"]
            add_expense(
                amount=s["amount"],
                category=s.get("category", "Software / services"),
                vendor=s.get("vendor", ""),
                description=f"{s.get('description', '')} ({s.get('cycle')} subscription)".strip(),
                date=charge_date,
                source="subscription",
            )
            added += 1
            s["next_charge"] = _advance(charge_date, s.get("cycle", "monthly"))
            changed = True
    if changed:
        _save_subs(subs)
    return added
