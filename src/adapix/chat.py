"""In-product chatbot — runs in the dashboard, asks the practice follow-up
questions to keep adapting the AI over time, and answers questions the
practice asks back.

Persistence: a simple JSON file (`chat_history.json` next to `practice_profile.json`)
holds the conversation. The full transcript is also folded into the agent's
system prompt via `practice.py`, so every interaction Adapix has with a
patient reflects everything the practice has told the chatbot.

Topics the bot wants to learn about (in priority order):
  1. Escalation rules — when to hand off to a human
  2. Custom escalation criteria — practice-specific situations
  3. Other doctors at the practice
  4. Office address + timezone
  5. Specialty / patient mix
  6. Anything else the practice wants to add
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from anthropic import Anthropic

from .config import Settings
from .practice import load_profile, PracticeProfile
from .skills import skills_index_block, get_skill, list_skills


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------
def _chat_path() -> Path:
    var = os.environ.get("ADAPIX_VAR", ".")
    return Path(var) / "chat_history.json"


def load_history() -> list[dict]:
    p = _chat_path()
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text()).get("messages", [])
    except Exception:
        return []


def save_history(messages: list[dict]) -> None:
    p = _chat_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"messages": messages}, indent=2))


def append_message(role: str, content: str) -> None:
    msgs = load_history()
    msgs.append({
        "role": role,
        "content": content,
        "ts": datetime.utcnow().isoformat() + "Z",
    })
    save_history(msgs)


def chat_transcript_for_prompt(max_chars: int = 6000) -> str:
    """The full chat history as a single text block suitable for embedding
    in the agent's or classifier's system prompt. Truncated to the most
    recent ~6kB so we don't blow up token counts."""
    msgs = load_history()
    if not msgs:
        return ""
    lines = []
    for m in msgs:
        who = "Practice" if m["role"] == "user" else "Adapix"
        lines.append(f"{who}: {m['content']}")
    text = "\n".join(lines)
    if len(text) > max_chars:
        text = "...(earlier conversation truncated)...\n" + text[-max_chars:]
    return text


# ---------------------------------------------------------------------------
# What's "still missing" — drives the bot's question prioritization
# ---------------------------------------------------------------------------
def missing_topics(profile: PracticeProfile, history: list[dict]) -> list[str]:
    """Return a list of topics the bot still wants to learn about,
    in priority order. We use loose heuristics — if the chat transcript
    already mentions a topic by keyword, skip asking again."""
    transcript = " ".join(m["content"].lower() for m in history)
    candidates = []

    # Escalation rules — only ask if neither in the wizard nor in chat
    if not profile.escalations and "escalat" not in transcript and "handoff" not in transcript:
        candidates.append("escalation_rules")

    # Other team members / staff
    if "other staff" not in transcript and "team member" not in transcript and "another person" not in transcript:
        candidates.append("other_staff")

    # Office address / timezone
    if "address" not in transcript and "timezone" not in transcript and "city" not in transcript:
        candidates.append("address_timezone")

    # Business specialty / customer demographics
    if "specialty" not in transcript and "customer mix" not in transcript and "client mix" not in transcript:
        candidates.append("specialty")

    # Custom escalation criteria (specific to this business)
    if "language barrier" not in transcript and "vip" not in transcript and "custom" not in transcript:
        candidates.append("custom_escalation")

    return candidates


# ---------------------------------------------------------------------------
# Suggested-topic chips shown under the conversation
# ---------------------------------------------------------------------------
SUGGESTION_BANK = {
    "escalation_rules": [
        "Tell me when to hand off to a human",
        "I'll handle pricing questions myself",
        "When should you NOT message a customer",
    ],
    "other_staff": [
        "We have multiple team members — let me list them",
        "Just one person handling this for now",
    ],
    "address_timezone": [
        "We're in the Eastern timezone",
        "Our address is...",
    ],
    "specialty": [
        "We mostly serve small businesses",
        "Most of our customers are local",
        "Our typical customer is...",
    ],
    "custom_escalation": [
        "Flag any message asking for a refund",
        "If a customer mentions a complaint, ping me",
    ],
}

DEFAULT_SUGGESTIONS = [
    "How do you handle missed appointments?",
    "Show me a sample message you'd send",
    "Tell me when to hand off to a human",
    "What if a customer asks something off-topic?",
]


def suggestions_for(missing: list[str]) -> list[str]:
    out: list[str] = []
    for topic in missing[:2]:
        out.extend(SUGGESTION_BANK.get(topic, [])[:2])
    return out[:4] or DEFAULT_SUGGESTIONS


# ---------------------------------------------------------------------------
# System prompt for the chatbot
# ---------------------------------------------------------------------------
def build_system_prompt(profile: PracticeProfile, missing: list[str]) -> str:
    """The Claude system prompt for the in-product chatbot. Knows the
    current practice profile and what info is still missing."""
    missing_descriptions = {
        "escalation_rules": (
            "When the business wants Adapix to STOP handling a conversation "
            "and route it to a real human on their team (complaints, "
            "emergencies, callback requests, pricing questions, etc.)."
        ),
        "other_staff": (
            "Whether the business has more than one team member, and what "
            "to call each of them in customer messages."
        ),
        "address_timezone": (
            "Where the business is physically located, especially the timezone "
            "so the AI doesn't text customers at 3am their time."
        ),
        "specialty": (
            "What kind of customers this business serves and what makes them "
            "unique, so Adapix can tailor its messaging appropriately."
        ),
        "custom_escalation": (
            "Any business-specific situation that should ALWAYS get a human "
            "(language barriers, billing edge cases, VIP customers, etc.)."
        ),
    }

    profile_block = (
        f"BUSINESS PROFILE (from the welcome wizard):\n"
        f"  Name: {profile.practice_name}\n"
        f"  Owner: {profile.doctor}\n"
        f"  Mode: {profile.mode}  "
        f"({'starting a new business' if profile.mode == 'new' else 'running an existing business'})\n"
        f"  Business type: {profile.practice_type_label or profile.practice_type or '(unset)'}\n"
        f"  Voice/tone: {profile.tone}\n"
        f"  Workflows enabled: {', '.join(profile.workflows) or '(none)'}\n"
        f"  Custom workflow (their words): "
        f"{profile.workflow_custom or '(none)'}\n"
        f"  Real-world problems (their words): "
        f"{(profile.practice_problems or '(none stated yet)')[:400]}"
    )

    missing_block = "\n".join(
        f"  - {topic}: {missing_descriptions.get(topic, '(unknown)')}"
        for topic in missing
    ) or "  (nothing critical — keep the conversation open and offer to help)"

    # Skills catalog — short index of every skill available for this mode.
    # The full body of any one skill is only injected when the user (or
    # the agent's own judgment) decides to invoke it; that keeps the system
    # prompt small while still letting the agent know what's available.
    skills_block = skills_index_block(mode=profile.mode)

    # Two slightly different framings depending on whether the user is
    # automating an existing business or starting one from scratch.
    if profile.mode == "new":
        role = (
            "You are Adapix, an AI co-founder helping the owner stand up "
            "their brand-new business. The person you are talking to is "
            "the founder. They may not have a name, customers, or even a "
            "fully formed idea yet. You are NOT a patient-facing agent "
            "here — you are in an internal admin chat surface, helping "
            "them think and ship."
        )
        goals = (
            "  1. Be concrete. Push past business-school answers and "
            "performative planning. Get to specifics fast.\n"
            "  2. Use the skills below when they fit. If the user's "
            "request matches a skill, name it explicitly before running "
            "it (e.g. \"let's run `name-brainstorm`\").\n"
            "  3. Don't try to do everything at once. One skill, one "
            "decision, one next move per exchange.\n"
            "  4. When the user gives you info, repeat it back in their "
            "own words to confirm you got it.\n"
            "  5. NEVER ask more than two questions in a single message."
        )
    else:
        role = (
            "You are Adapix, the AI follow-up assistant deployed at this "
            "business. The person you are talking to is the owner or "
            "manager. You are in an internal admin chat surface — your job "
            "here is to be immediately and concretely useful to them."
        )
        goals = (
            "  1. LEAD WITH VALUE. Your first move is always to show what "
            "you can do for this specific business — concrete offers, "
            "real examples, specific things you'll handle. Never open "
            "with a question.\n"
            "  2. When the owner asks what you can do, give a direct, "
            "specific answer based on their setup. Then offer to do one "
            "of those things right now.\n"
            "  3. Only ask ONE question per message, and only AFTER "
            "you've been helpful. Never lead with data-collection.\n"
            "  4. When the owner teaches you something new, confirm it "
            "back in plain language: 'Got it — so I'll…'\n"
            "  5. NEVER ask more than one question in a single message."
        )

    capabilities_block = """\
WHAT ADAPIX ACTUALLY DOES (use these when the owner asks what you can do —
lead with these, not with generic follow-up talk):
  1. Runs the FULL follow-up cycle automatically — first outreach to final
     nudge, while the office is closed, overnight, on weekends. Nothing
     falls through the cracks.
  2. Sends from YOUR OWN email and phone number — not a generic chatbot
     address. Customers see a message from the business they know.
  3. Human approval before EVERY send — Adapix drafts, you tap approve.
     You're in charge of every word. Drafts you reject cost nothing.
  4. Handles inbound replies automatically — classifies them, escalates
     the urgent ones to a real human immediately, keeps the rest moving.
  5. Every morning you get a clear desk — see what happened overnight,
     approve the drafts in the queue, then close the tab.
  6. On-device storage — data never leaves the building. HIPAA-ready
     architecture with BAA available for regulated industries.
  7. Plug-and-play setup — no IT team, no developers, up and running in
     10 minutes. Works with your existing email and phone number.
  8. Pricing that makes sense — $29/mo base + $0.20 per approved message
     sent. You only pay when Adapix actually does something.

When the owner asks "what can you do?", lead with the 2-3 capabilities
most relevant to their business type and the problems they described.
Give concrete examples — e.g. "I'll send a follow-up the moment a lead
goes quiet for 48 hours, draft a message in your tone, and put it in your
approval queue. You tap approve and it's gone."
Do NOT talk about lead qualification, CRM features, or analytics unless
they ask. Stick to what Adapix actually does."""

    return f"""\
{role}

GOALS, in priority order:
{goals}

{capabilities_block}

{profile_block}

STILL MISSING (in priority order — only ask about these AFTER you've been
helpful, one at a time, never as the opener):
{missing_block}

{skills_block}

CONVERSATION RULES:
- Use short paragraphs, casual punctuation, no markdown headings.
- Refer to the owner by name when natural (e.g. "{profile.doctor}").
- If the user gives you escalation rules, summarize back: "Got it — so I'll
  always ping you when X, Y, Z."
- If the user goes off-topic, follow them. Don't drag them back to the form.
- Keep replies under ~120 words usually.
"""


# ---------------------------------------------------------------------------
# Main chatbot call
# ---------------------------------------------------------------------------
def generate_opener() -> dict:
    """Generate the bot's opening message — used the first time the user
    opens /chat and there's no prior history."""
    profile = load_profile()
    history = load_history()
    missing = missing_topics(profile, history)
    sys = build_system_prompt(profile, missing) + (
        "\n\nThis is your FIRST message in this chat. Lead with 2-3 specific, "
        "concrete things you can do for this business RIGHT NOW based on their "
        f"setup (workflows enabled, problems they described, business type). "
        f"Address the owner as {profile.doctor}. Be direct and useful — "
        "show your value immediately. End with ONE brief question only if "
        "genuinely needed. ~80-100 words. Do NOT open with a question."
    )
    settings = Settings()
    client = Anthropic(api_key=settings.anthropic_api_key)
    resp = client.messages.create(
        model=settings.adapix_model,
        max_tokens=400,
        system=sys,
        messages=[{
            "role": "user",
            "content": "(System: please open the conversation.)",
        }],
    )
    text = "".join(b.text for b in resp.content if hasattr(b, "text"))
    append_message("assistant", text)
    return {
        "message": text,
        "suggestions": suggestions_for(missing),
    }


def reply_to(user_message: str) -> dict:
    """Append the user's message + generate Adapix's reply + extract
    any new structured facts from the user's message into memory."""
    append_message("user", user_message)

    # ---- 1) Extract structured memory from the user's message ----
    # Runs in foreground; if it fails for any reason, we still continue
    # to compose the reply so the chat never breaks.
    new_facts: list[dict] = []
    try:
        from .memory import remember_from_message
        new_facts = remember_from_message(user_message)
    except Exception as e:
        print(f"[chat] memory extraction failed: {e}")

    # ---- 2) Build the chatbot's system prompt with current memory ----
    profile = load_profile()
    history = load_history()
    missing = missing_topics(profile, history)
    sys = build_system_prompt(profile, missing)
    try:
        from .memory import memory_for_prompt
        mem_block = memory_for_prompt()
        if mem_block:
            sys = sys + "\n\n" + mem_block
        if new_facts:
            tip = ("\n\nIn your reply, briefly acknowledge what you just "
                   "noted to memory. Phrase it casually, like 'Got it — "
                   "I'll remember that <X>.' Then continue the conversation.")
            sys = sys + tip
    except Exception:
        pass

    # ---- 3) Compose Adapix's reply ----
    msgs = []
    for m in history:
        role = "user" if m["role"] == "user" else "assistant"
        msgs.append({"role": role, "content": m["content"]})

    settings = Settings()
    client = Anthropic(api_key=settings.anthropic_api_key)
    resp = client.messages.create(
        model=settings.adapix_model,
        max_tokens=500,
        system=sys,
        messages=msgs,
    )
    reply_text = "".join(b.text for b in resp.content if hasattr(b, "text"))
    append_message("assistant", reply_text)

    # ---- 4) Detect + persist any EXPENSE described in the user message ----
    # This is the founder-bookkeeping integration — drop "$52 1TB SSD from
    # Amazon" into chat and Adapix auto-logs it to expenses.json.
    expense_record = None
    try:
        from .expenses import remember_expense_from_message
        expense_record = remember_expense_from_message(user_message)
    except Exception as e:
        print(f"[chat] expense extraction failed: {e}")

    # ---- 5) Push a Web Push notification if we just learned something ----
    # This gives the user a visible signal on their phone that Adapix is
    # actively building up its understanding of their practice, even
    # while they're not looking at the chat.
    if new_facts:
        try:
            from .notifications import push_notification
            n = len(new_facts)
            preview = new_facts[0].get("text") or "Saved a new fact."
            if len(preview) > 90:
                preview = preview[:87] + "…"
            push_notification(
                title="Adapix learned something",
                body=f"{preview}" + (f"  (+{n-1} more)" if n > 1 else ""),
                url="/chat",
                tag="adapix-memory",
            )
        except Exception as e:
            print(f"[chat] push notification failed: {e}")

    if expense_record:
        try:
            from .notifications import push_notification
            push_notification(
                title=f"+ ${expense_record['amount']:.2f} logged",
                body=f"{expense_record['description'] or expense_record['category']}"
                     + (f"  ·  {expense_record['vendor']}" if expense_record['vendor'] else ""),
                url="/expenses",
                tag="adapix-expense",
            )
        except Exception:
            pass

    return {
        "reply":      reply_text,
        "suggestions": suggestions_for(missing),
        "remembered": new_facts,
        "expense":    expense_record,
    }
