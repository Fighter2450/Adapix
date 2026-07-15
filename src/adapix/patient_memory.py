"""Per-customer memory — what Adapix has learned about THIS specific person
from actually talking to them, as opposed to memory.py's org-level memory
(facts about how the BUSINESS operates, taught via the /chat interface).

After every inbound reply (text or call outcome), Claude checks whether the
customer revealed anything worth remembering for future conversations with
them specifically — a preference, a detail about their situation, a concern.
Stored on Patient.memory_json and fed back into every future message/call to
that contact, so something mentioned once doesn't have to be re-asked or
(worse) re-explained by the customer a second time.

Schema (Patient.memory_json — a JSON list):
[
  {
    "id":       "f3f8a1",
    "text":     "The customer has a dog that needs to be locked up before a technician arrives.",
    "category": "detail",
    "source":   "conversation",
    "ts":       "2026-07-15T02:00:00Z"
  },
  ...
]

Categories (loose, used for grouping in the UI):
  preference  - how they like to be contacted/treated (channel, timing, tone)
  detail      - a concrete fact about their situation, property, or order
  concern     - something they're worried about or unhappy with
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime

from anthropic import Anthropic

from .config import Settings

_VALID_CATEGORIES = {"preference", "detail", "concern"}

EXTRACTION_SYSTEM = """\
You read one inbound message from a customer to a small business's AI \
follow-up assistant and decide: did the customer just reveal any NEW fact \
worth remembering for future conversations with THEM specifically?

A FACT is:
  - A specific, concrete detail about this customer, their situation, or
    their preferences that would make a future message or call more
    personal and useful — e.g. "has a dog that needs to be secured before
    a technician arrives", "prefers texts over phone calls", "the budget
    is tight this month", "already got a competing quote $200 lower",
    "works nights so mornings are better for a callback"
  - Something worth remembering weeks from now, not just relevant to this
    one exchange

A FACT is NOT:
  - A greeting, thanks, or filler ("ok", "sounds good", "thanks!")
  - Something already listed in EXISTING MEMORY — never restate it
  - Speculation or something you're inferring without them actually saying it
  - Anything about the BUSINESS itself (pricing, hours, policies — that is
    separate memory, not this)

For each new fact, pick the best category:
  preference  - how they like to be contacted or treated
  detail      - a concrete fact about their situation/property/order
  concern     - something they're worried about, unhappy with, or hesitant on

Output ONLY a JSON array, no preamble, no markdown fence. Each element:
  {"text": "<one-sentence fact, third person, referring to 'the customer'>", "category": "<preference|detail|concern>"}

If the message contains no new facts, return [].
"""


def format_memory(facts: list[dict], max_chars: int = 1500) -> str:
    """Claude-friendly prompt block — fed into compose_message/
    respond_to_inbound/the call system prompt for this specific contact."""
    entries = [f for f in (facts or []) if (f.get("text") or "").strip()]
    if not entries:
        return ""
    lines = [
        "WHAT YOU KNOW ABOUT THIS CUSTOMER (learned from earlier conversations "
        "with them — use it naturally to sound like you remember them, don't "
        "just recite it back verbatim):"
    ]
    for f in entries:
        lines.append(f"  - {f['text'].strip()}")
    text = "\n".join(lines)
    if len(text) > max_chars:
        text = text[:max_chars] + "...(truncated)"
    return text


def extract_new_facts(message: str, existing: list[dict]) -> list[dict]:
    """Ask Claude what new facts (if any) the customer just revealed.
    Returns a list of {text, category} dicts. Empty list if nothing new."""
    if not message or not message.strip():
        return []
    existing_block = "\n".join(f"  - {f.get('text', '')}" for f in (existing or [])) or "  (none yet)"
    user_block = (
        f"EXISTING MEMORY (do not re-extract these):\n{existing_block}\n\n"
        f"NEW MESSAGE FROM CUSTOMER:\n{message.strip()}\n\n"
        f"Output JSON array of NEW facts only."
    )
    try:
        settings = Settings()
        client = Anthropic(api_key=settings.anthropic_api_key)
        resp = client.messages.create(
            model=settings.adapix_model,
            max_tokens=300,
            system=EXTRACTION_SYSTEM,
            messages=[{"role": "user", "content": user_block}],
        )
        text = "".join(b.text for b in resp.content if hasattr(b, "text")).strip()
    except Exception as e:
        print(f"[patient_memory] extraction call failed: {e}")
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
            if not isinstance(item, dict):
                continue
            t = (item.get("text") or "").strip()
            c = (item.get("category") or "detail").strip().lower()
            if not t:
                continue
            if c not in _VALID_CATEGORIES:
                c = "detail"
            cleaned.append({"text": t, "category": c})
        return cleaned
    except Exception:
        return []


def remember_from_message(patient, message: str) -> list[dict]:
    """Extract new facts from a customer's message/call transcript and
    append them to patient.memory_json in place. Caller owns the session
    commit. Returns the facts that were newly added."""
    existing = list(patient.memory_json or [])
    new_facts = extract_new_facts(message, existing)
    added: list[dict] = []
    for f in new_facts:
        entry = {
            "id": uuid.uuid4().hex[:6],
            "text": f["text"],
            "category": f["category"],
            "source": "conversation",
            "ts": datetime.utcnow().isoformat() + "Z",
        }
        existing.append(entry)
        added.append(entry)
    if added:
        patient.memory_json = existing
    return added
