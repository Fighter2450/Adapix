"""Long-term structured memory for Adapix.

The practice teaches Adapix things through the in-app chat. Some of those
things are just conversation; others are FACTS that should permanently
shape how Adapix behaves toward patients ("always escalate pricing," "we
accept CareCredit," "Dr. Patel hates the word 'noninvasive'").

This module:
  1) After every user message in /chat, calls Claude to extract any new
     declarative facts from the message.
  2) Stores them in memory.json next to practice_profile.json so they
     survive across sessions.
  3) Exposes a formatted block (`memory_for_prompt`) that the agent's
     system prompt includes for every outbound + reply Claude generates.

The user can see, edit, and delete remembered facts from the chat UI.

Schema (memory.json):
{
  "facts": [
    {
      "id":       "f3f8a1",
      "text":     "Always escalate pricing questions to the practice owner.",
      "category": "rule",
      "source":   "chat",
      "ts":       "2026-05-13T15:30:00Z"
    },
    ...
  ]
}

Categories used (loose, used for grouping in the UI):
  rule                - hard handling rule for the AI
  preference          - soft preference (tone, word choice)
  constraint          - what NOT to do
  detail              - factual information about the practice
  escalation_criterion - a specific case that should always go to a human
"""
from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from anthropic import Anthropic

from .config import Settings


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------
def _memory_path(org_id: str | None = None) -> Path:
    var = os.environ.get("ADAPIX_VAR", ".")
    if org_id:
        # Per-tenant store — one org's taught facts must never leak into
        # another org's prompts. Legacy single-tenant path kept for the CLI.
        return Path(var) / "memory" / f"{org_id}.json"
    return Path(var) / "memory.json"


def load_memory(org_id: str | None = None) -> dict:
    p = _memory_path(org_id)
    if not p.exists():
        return {"facts": []}
    try:
        return json.loads(p.read_text())
    except Exception:
        return {"facts": []}


def save_memory(memory: dict, org_id: str | None = None) -> None:
    p = _memory_path(org_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(memory, indent=2))


def all_facts(org_id: str | None = None) -> list[dict]:
    return load_memory(org_id).get("facts", [])


def add_fact(text: str, category: str = "detail", source: str = "chat",
             org_id: str | None = None) -> dict:
    text = (text or "").strip()
    if not text:
        return {}
    mem = load_memory(org_id)
    fact = {
        "id":       uuid.uuid4().hex[:6],
        "text":     text,
        "category": category,
        "source":   source,
        "ts":       datetime.utcnow().isoformat() + "Z",
    }
    mem.setdefault("facts", []).append(fact)
    save_memory(mem, org_id)
    return fact


def remove_fact(fact_id: str, org_id: str | None = None) -> bool:
    mem = load_memory(org_id)
    before = len(mem.get("facts", []))
    mem["facts"] = [f for f in mem.get("facts", []) if f.get("id") != fact_id]
    save_memory(mem, org_id)
    return len(mem["facts"]) < before


# ---------------------------------------------------------------------------
# Formatting for AI system prompts
# ---------------------------------------------------------------------------
def memory_for_prompt(max_chars: int = 3000, org_id: str | None = None) -> str:
    """Return the full structured memory as a Claude-friendly prompt block."""
    facts = all_facts(org_id)
    if not facts:
        return ""
    lines = ["PERMANENT MEMORY (everything this practice has taught me — "
             "treat each item as a hard rule unless category says otherwise):"]
    by_cat: dict[str, list[dict]] = {}
    for f in facts:
        by_cat.setdefault(f.get("category", "detail"), []).append(f)
    order = ["rule", "constraint", "escalation_criterion", "preference", "detail"]
    for cat in order:
        if cat not in by_cat:
            continue
        lines.append(f"  [{cat}]")
        for f in by_cat[cat]:
            lines.append(f"    - {f['text']}")
    text = "\n".join(lines)
    if len(text) > max_chars:
        text = text[:max_chars] + "...(truncated)"
    return text


# ---------------------------------------------------------------------------
# Claude-driven fact extraction
# ---------------------------------------------------------------------------
EXTRACTION_SYSTEM = """\
You read one message from a medical practice's staff to their AI assistant
("Adapix") and decide: did the practice just teach Adapix any NEW factual
assertion that should be permanently remembered?

A FACT is:
  - A specific, declarative statement about how the practice operates
    or how it wants Adapix to behave
  - Something Adapix could ACT on or REFERENCE later
  - One sentence each — split compound statements into separate facts

A FACT is NOT:
  - A greeting or filler ("hi", "thanks", "ok")
  - A question the user is asking
  - Speculation or general knowledge
  - Information already in EXISTING MEMORY (don't restate facts they
    already told us)

For each new fact, pick the BEST category:
  rule                  - hard handling rule ("Always do X")
  constraint            - what NOT to do ("Never quote prices over text")
  escalation_criterion  - specific case that should ALWAYS go to a human
  preference            - soft style/tone choice
  detail                - factual info about the practice (address, hours, etc.)

Output ONLY a JSON array, no preamble. Each element is:
  {"text": "<one-sentence fact in third person>", "category": "<one of above>"}

If the message contains no new facts, return [].
"""


def extract_new_facts(user_message: str, existing_facts: list[dict] | None = None) -> list[dict]:
    """Ask Claude what new facts (if any) the practice just stated.
    Returns a list of {text, category} dicts. Empty list if nothing new."""
    if not user_message or not user_message.strip():
        return []
    existing_facts = existing_facts if existing_facts is not None else all_facts()
    existing_block = "\n".join(f"  - {f['text']}" for f in existing_facts) or "  (none yet)"
    user_block = (
        f"EXISTING MEMORY (do not re-extract these):\n{existing_block}\n\n"
        f"NEW MESSAGE FROM PRACTICE:\n{user_message.strip()}\n\n"
        f"Output JSON array of NEW facts only."
    )
    try:
        settings = Settings()
        client = Anthropic(api_key=settings.anthropic_api_key)
        resp = client.messages.create(
            model=settings.adapix_model,
            max_tokens=400,
            system=EXTRACTION_SYSTEM,
            messages=[{"role": "user", "content": user_block}],
        )
        text = "".join(b.text for b in resp.content if hasattr(b, "text")).strip()
    except Exception as e:
        print(f"[memory] extraction call failed: {e}")
        return []

    # Pull the first JSON array out of the response (defensive against
    # Claude wrapping output in prose or markdown despite instructions).
    m = re.search(r"\[\s*(?:\{.*?\}\s*,?\s*)*\]", text, re.DOTALL)
    if not m:
        return []
    try:
        parsed = json.loads(m.group(0))
        if not isinstance(parsed, list):
            return []
        cleaned: list[dict] = []
        for item in parsed:
            if not isinstance(item, dict): continue
            t = (item.get("text") or "").strip()
            c = (item.get("category") or "detail").strip().lower()
            if not t: continue
            if c not in {"rule","constraint","escalation_criterion","preference","detail"}:
                c = "detail"
            cleaned.append({"text": t, "category": c})
        return cleaned
    except Exception:
        return []


def remember_from_message(user_message: str, org_id: str | None = None) -> list[dict]:
    """Extract NEW facts from a user message and persist them to the org's
    memory store. Returns the facts that were newly added (for the UI)."""
    new_facts = extract_new_facts(user_message, existing_facts=all_facts(org_id))
    added: list[dict] = []
    for f in new_facts:
        added.append(add_fact(f["text"], category=f["category"], source="chat", org_id=org_id))
    return added
