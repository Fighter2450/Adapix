"""Dynamic dashboard config + widget catalog.

The dashboard is not a fixed UI — it's a *template* that Adapix continuously
reshapes based on conversations with the practice. The practice's chat with
Adapix is the program; the dashboard is the output.

Three pieces:

  1. WIDGET_CATALOG — every widget we know how to render. Fixed at build
     time. Each entry has an id, label, description, data-fetcher hint,
     and a "default position" for first-time installs.

  2. dashboard_config.json — which widgets are currently shown, in what
     order, with what settings. Persisted between sessions. Mutated by
     Adapix's tool calls during chat.

  3. The "why is this here?" provenance — every widget remembers WHICH
     chat exchange caused it to appear/disappear, so the user can hover
     over it and see Adapix's reasoning.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Widget catalog — every widget type Adapix can place on the dashboard.
# When we add a new widget, register it here AND add its data-fetcher to
# get_widget_data() below.
# ---------------------------------------------------------------------------
WIDGET_CATALOG: list[dict[str, Any]] = [
    {
        "id":          "escalations_open",
        "label":       "Open escalations",
        "description": "Patient messages flagged for human attention (emergency, clinical, callback, pricing).",
        "default":     {"position": "top", "pinned": True},
        "priority":    100,   # always near the top — emergencies must be visible
    },
    {
        "id":          "approvals_pending",
        "label":       "Drafts waiting for approval",
        "description": "Messages Adapix has composed that need your sign-off before they go to the patient.",
        "default":     {"position": "top"},
        "priority":    90,
    },
    {
        "id":          "unscheduled_referrals",
        "label":       "Unscheduled referrals",
        "description": "Patients referred to your practice who haven't booked their consult yet.",
        "default":     {"position": "main"},
        "priority":    70,
        "depends_on_workflow": "case_acceptance",
    },
    {
        "id":          "post_op_check_ins",
        "label":       "Post-op check-ins",
        "description": "Patients on day 1, 3, or 7 after a procedure who Adapix should reach out to.",
        "default":     {"position": "main"},
        "priority":    65,
        "depends_on_workflow": "post_op_check_in",
    },
    {
        "id":          "recall_due_this_week",
        "label":       "Recalls due this week",
        "description": "Patients whose 6-month recall is coming due.",
        "default":     {"position": "main"},
        "priority":    50,
        "depends_on_workflow": "recall_6mo",
    },
    {
        "id":          "financing_follow_ups",
        "label":       "Treatment-plan financing follow-ups",
        "description": "Patients who paused on treatment cost — Adapix can send one financing reminder.",
        "default":     {"position": "main"},
        "priority":    55,
        "depends_on_workflow": "financing_followup",
    },
    {
        "id":          "today_overview",
        "label":       "Today",
        "description": "Quick stats: messages sent today, escalations open, drafts pending.",
        "default":     {"position": "right"},
        "priority":    80,
    },
    {
        "id":          "memory_summary",
        "label":       "What I remember",
        "description": "Top facts Adapix has learned about the practice, sourced from chat.",
        "default":     {"position": "right"},
        "priority":    40,
    },
    {
        "id":          "recent_activity",
        "label":       "Recent activity",
        "description": "Timeline of what Adapix did in the last 24h — drafts composed, messages sent, facts learned.",
        "default":     {"position": "right"},
        "priority":    30,
    },
    {
        "id":          "device_status",
        "label":       "Adapt 1.0 status",
        "description": "Device connection, last sync, software version, storage available.",
        "default":     {"position": "right"},
        "priority":    20,
    },
]


def catalog_index() -> dict[str, dict[str, Any]]:
    """Quick lookup: widget_id -> catalog entry."""
    return {w["id"]: w for w in WIDGET_CATALOG}


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------
def _var_dir() -> Path:
    return Path(os.environ.get("ADAPIX_VAR", "."))


def _path() -> Path:
    return _var_dir() / "dashboard_config.json"


def _default_config() -> dict[str, Any]:
    """Generate the default dashboard for a new install — uses the
    practice's enabled workflows to decide which widgets to include."""
    # Lazy import so we don't load practice.py at module import time
    try:
        from .practice import load_profile
        profile = load_profile()
        active_workflows = set(profile.workflows or [])
    except Exception:
        active_workflows = {"case_acceptance"}  # safe minimum

    widgets: list[dict[str, Any]] = []
    for entry in WIDGET_CATALOG:
        # Skip widgets whose workflow isn't enabled
        req = entry.get("depends_on_workflow")
        if req and req not in active_workflows:
            continue
        widgets.append({
            "id":       entry["id"],
            "position": entry["default"]["position"],
            "pinned":   bool(entry["default"].get("pinned", False)),
            "added_at": int(time.time()),
            "added_by": "default",
            "reason":   "Included in the default layout for new installs.",
        })

    return {
        "version":     1,
        "updated_at":  int(time.time()),
        "widgets":     widgets,
    }


def load_config() -> dict[str, Any]:
    p = _path()
    if not p.exists():
        cfg = _default_config()
        save_config(cfg)
        return cfg
    try:
        return json.loads(p.read_text())
    except Exception:
        cfg = _default_config()
        save_config(cfg)
        return cfg


def save_config(cfg: dict[str, Any]) -> None:
    cfg["updated_at"] = int(time.time())
    cfg["version"] = int(cfg.get("version", 0)) + 1
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(cfg, indent=2))


def reset_to_default() -> dict[str, Any]:
    cfg = _default_config()
    save_config(cfg)
    return cfg


# ---------------------------------------------------------------------------
# Mutations — used by both the user-facing UI and Adapix's chat tool calls.
# Every mutation records who/why so the user can hover over a widget and
# see the conversation that put it there.
# ---------------------------------------------------------------------------
def add_widget(widget_id: str, *, position: str = "main", reason: str = "",
               added_by: str = "user", pinned: bool = False) -> bool:
    if widget_id not in catalog_index():
        return False
    cfg = load_config()
    # No-op if the widget is already on the dashboard
    if any(w.get("id") == widget_id for w in cfg["widgets"]):
        return True
    cfg["widgets"].append({
        "id":       widget_id,
        "position": position if position in ("top", "main", "right") else "main",
        "pinned":   pinned,
        "added_at": int(time.time()),
        "added_by": added_by,
        "reason":   reason or "Added to the dashboard.",
    })
    save_config(cfg)
    return True


def remove_widget(widget_id: str, *, reason: str = "", removed_by: str = "user") -> bool:
    cfg = load_config()
    before = len(cfg["widgets"])
    cfg["widgets"] = [
        w for w in cfg["widgets"] if w.get("id") != widget_id
    ]
    if len(cfg["widgets"]) == before:
        return False
    # Track the removal in a separate log key so we can show "Adapix removed
    # the recall widget because you said you don't do recall — undo?"
    log = cfg.setdefault("removed_log", [])
    log.append({
        "id":         widget_id,
        "removed_at": int(time.time()),
        "removed_by": removed_by,
        "reason":     reason or "Removed from the dashboard.",
    })
    save_config(cfg)
    return True


def pin_widget(widget_id: str, pinned: bool = True, *, reason: str = "",
               changed_by: str = "user") -> bool:
    cfg = load_config()
    found = False
    for w in cfg["widgets"]:
        if w.get("id") == widget_id:
            w["pinned"] = pinned
            w["pinned_at"] = int(time.time())
            w["pinned_by"] = changed_by
            w["pinned_reason"] = reason
            found = True
    if not found:
        return False
    save_config(cfg)
    return True


def reorder_widgets(ordered_ids: list[str], *, reason: str = "",
                    changed_by: str = "user") -> bool:
    cfg = load_config()
    existing = {w["id"]: w for w in cfg["widgets"]}
    # Keep only widgets that already exist in the config + match the new order
    new_list = [existing[i] for i in ordered_ids if i in existing]
    # Append any widgets not mentioned in the new order at the end
    for w in cfg["widgets"]:
        if w["id"] not in ordered_ids:
            new_list.append(w)
    cfg["widgets"] = new_list
    if reason:
        cfg.setdefault("reorder_log", []).append({
            "at":         int(time.time()),
            "changed_by": changed_by,
            "reason":     reason,
            "order":      ordered_ids,
        })
    save_config(cfg)
    return True


# ---------------------------------------------------------------------------
# Data fetchers — given a widget id, return the data the UI needs to render
# it. Each widget has its own shape; the UI knows how to render each one.
# ---------------------------------------------------------------------------
def get_widget_data(widget_id: str, org_id: str | None = None) -> dict[str, Any]:
    """Dispatch to the right data fetcher for this widget. Returns an empty
    dict if the widget isn't known or the fetch fails — UI should render
    an empty state in that case.

    org_id scopes every DB-backed widget to the calling tenant; None means
    unscoped (only safe for single-tenant CLI/dev use)."""
    try:
        if widget_id == "escalations_open":
            return _data_escalations(org_id)
        if widget_id == "approvals_pending":
            return _data_approvals(org_id)
        if widget_id == "unscheduled_referrals":
            return _data_workflow_queue("case_acceptance", org_id)
        if widget_id == "post_op_check_ins":
            return _data_workflow_queue("post_op_check_in", org_id)
        if widget_id == "recall_due_this_week":
            return _data_workflow_queue("recall_6mo", org_id)
        if widget_id == "financing_follow_ups":
            return _data_workflow_queue("financing_followup", org_id)
        if widget_id == "today_overview":
            return _data_today_overview(org_id)
        if widget_id == "memory_summary":
            return _data_memory_summary()
        if widget_id == "recent_activity":
            return _data_recent_activity()
        if widget_id == "device_status":
            return _data_device_status()
    except Exception as e:
        print(f"[dashboard] data fetch failed for {widget_id}: {e}")
    return {}


def _data_escalations(org_id: str | None = None) -> dict[str, Any]:
    from .db import get_session
    from .models import EscalationEvent, Campaign, Patient
    with get_session() as s:
        q = s.query(EscalationEvent)
        if org_id:
            q = q.join(Campaign, EscalationEvent.campaign_id == Campaign.id).filter(
                Campaign.practice_id == org_id)
        rows = (
            q.filter(EscalationEvent.resolved == False)  # noqa: E712
            .order_by(EscalationEvent.created_at.desc())
            .limit(10)
            .all()
        )
        items = []
        for e in rows:
            campaign = s.get(Campaign, e.campaign_id)
            patient  = s.get(Patient, campaign.patient_id) if campaign else None
            items.append({
                "id":         e.id,
                "category":   e.category,
                "confidence": e.confidence,
                "patient":    f"{patient.first_name} {patient.last_name}" if patient else "(unknown)",
                "reasoning":  e.reasoning,
                "suggested":  e.suggested_action,
                "created_at": e.created_at.isoformat() if e.created_at else "",
            })
        return {"items": items, "count": len(items)}


def _data_approvals(org_id: str | None = None) -> dict[str, Any]:
    from .db import get_session
    from .models import Message, Campaign, Patient
    with get_session() as s:
        q = s.query(Message)
        if org_id:
            q = q.join(Campaign, Message.campaign_id == Campaign.id).filter(
                Campaign.practice_id == org_id)
        rows = (
            q.filter(Message.status == "pending_approval")
            .order_by(Message.created_at.desc())
            .limit(10)
            .all()
        )
        items = []
        for m in rows:
            campaign = s.get(Campaign, m.campaign_id)
            patient  = s.get(Patient, campaign.patient_id) if campaign else None
            items.append({
                "id":      m.id,
                "patient": f"{patient.first_name} {patient.last_name}" if patient else "(unknown)",
                "channel": m.channel,
                "body":    m.body,
            })
        return {"items": items, "count": len(items)}


def _data_workflow_queue(workflow_id: str, org_id: str | None = None) -> dict[str, Any]:
    """Patients currently in this workflow. v0: just returns campaign count.
    Full implementation comes later when we wire the patient roster."""
    from .db import get_session
    from .models import Campaign
    with get_session() as s:
        q = s.query(Campaign)
        if org_id:
            q = q.filter(Campaign.practice_id == org_id)
        count = (
            q.filter(Campaign.workflow_id == workflow_id)
            .filter(Campaign.status == "active")
            .count()
        )
        return {"count": count, "workflow_id": workflow_id, "items": []}


def _data_today_overview(org_id: str | None = None) -> dict[str, Any]:
    from datetime import datetime, timedelta
    from .db import get_session
    from .models import Message, EscalationEvent, Campaign
    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    with get_session() as s:
        mq = s.query(Message)
        eq = s.query(EscalationEvent)
        if org_id:
            mq = mq.join(Campaign, Message.campaign_id == Campaign.id).filter(
                Campaign.practice_id == org_id)
            eq = eq.join(Campaign, EscalationEvent.campaign_id == Campaign.id).filter(
                Campaign.practice_id == org_id)
        sent_today = (
            mq.filter(Message.direction == "outbound")
            .filter(Message.status.in_(["sent", "delivered"]))
            .filter(Message.created_at >= today_start)
            .count()
        )
        pending = mq.filter(Message.status == "pending_approval").count()
        open_esc = eq.filter(EscalationEvent.resolved == False).count()  # noqa: E712
        return {
            "sent_today": sent_today,
            "pending_approvals": pending,
            "open_escalations": open_esc,
        }


def _data_memory_summary() -> dict[str, Any]:
    try:
        from .memory import all_facts
        facts = all_facts()[:6]
        return {"facts": facts, "total": len(all_facts())}
    except Exception:
        return {"facts": [], "total": 0}


def _data_recent_activity() -> dict[str, Any]:
    """Last 10 things Adapix did — drafts composed, messages sent, facts
    learned. v0: stub. Real implementation needs an event log."""
    return {"items": []}


def _data_device_status() -> dict[str, Any]:
    import platform
    import shutil
    try:
        usage = shutil.disk_usage("/")
        gb_free = usage.free / (1024 ** 3)
    except Exception:
        gb_free = None
    return {
        "device_id":   "adapt-1.0-dev",
        "online":      True,
        "uptime":      None,
        "free_gb":     round(gb_free, 1) if gb_free else None,
        "os":          platform.platform(),
        "app_version": "0.1.0",
    }
