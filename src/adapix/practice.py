"""Loader for the practice profile saved by the welcome wizard.

The wizard writes `practice_profile.json` containing the practice name, lead
doctor, voice/tone choice, enabled workflows, escalation rules, and any
custom "Other" text the user typed. This module:

  1) Reads that JSON into a structured PracticeProfile dataclass
  2) Generates Claude-ready system-prompt fragments from it, so the agent
     and the escalation classifier sound and behave like the practice the
     wizard described — not a generic template.

If no profile exists yet (fresh device, wizard not run), `load_profile()`
returns a sensible default so demos still work.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path


# ---------------------------------------------------------------------------
# Tone definitions — short Claude-facing description for each voice choice.
# These get pasted into the system prompt so Claude's output style matches.
# ---------------------------------------------------------------------------
TONE_GUIDANCE = {
    "warm_professional": (
        "Tone: WARM PROFESSIONAL. Friendly but never casual. Use the customer's "
        "first name. Short sentences, polite. No emojis except a single :) when "
        "delivering good news. Use contractions ('we're', 'that's')."
    ),
    "casual_friendly": (
        "Tone: CASUAL FRIENDLY. Like a text from someone the customer knows. "
        "First name, contractions, occasional :) is fine. Keep it light but "
        "always respectful. Never slang that could read as unprofessional."
    ),
    "clinical_formal": (
        "Tone: DIRECT & FORMAL. Use the customer's full name (first + last). "
        "Precise, direct, no contractions. No emojis. State facts in complete "
        "sentences. Appropriate for professional services and formal industries."
    ),
}

# Human-readable labels for each workflow ID — used in dashboard + prompts.
WORKFLOW_LABELS = {
    "case_acceptance":     "Follow up on unscheduled leads",
    "recall_6mo":          "Periodic follow-up reminders",
    "recall_reminders":    "Follow-up / recall reminders",
    "post_op_check_in":    "Post-service check-ins",
    "no_show_recovery":    "No-show / missed appointment recovery",
    "financing_followup":  "Financing and payment follow-ups",
}

# Human-readable labels + classifier hints for each escalation category.
ESCALATION_LABELS = {
    "emergency": (
        "Customer mentions an urgent situation, safety concern, or anything "
        "that requires immediate human attention."
    ),
    "clinical_question": (
        "Customer asks a question that requires an expert staff member to "
        "answer (technical specifics, service details, professional advice)."
    ),
    "callback_request": (
        "Customer explicitly asks for a phone call or wants to speak to someone."
    ),
    "pricing_question": (
        "Customer asks about cost, pricing, payment plans, or what they will owe."
    ),
}


# ---------------------------------------------------------------------------
@dataclass
class PracticeProfile:
    """Structured form of the wizard's output."""
    practice_name: str = "your practice"
    doctor: str = "Dr."
    phone: str = ""
    hours: str = ""
    tone: str = "warm_professional"

    workflows: list[str] = field(default_factory=lambda: ["case_acceptance"])
    workflow_custom: str = ""

    escalations: list[str] = field(default_factory=lambda: [
        "emergency", "clinical_question", "callback_request", "pricing_question",
    ])
    escalation_custom: str = ""
    # Free-form description of the practice's real-world problems
    # (collected via the welcome wizard's "what's slipping through" textarea).
    practice_problems: str = ""
    # Practice type:
    #   practice_type        — slug id from the welcome wizard's searchable
    #                          picker (e.g. "oral_surgeon", "coffee_shop").
    #                          "" if not yet configured, "other" if "Not
    #                          listed" was picked.
    #   practice_type_label  — the human-readable name shown in the dropdown
    #                          and fed into Adapix's system prompt. We use
    #                          this directly instead of a hardcoded lookup
    #                          so the catalog stays open-ended.
    #   practice_type_custom — free text the user typed when picking "Not
    #                          listed".
    practice_type: str = ""
    practice_type_label: str = ""
    practice_type_custom: str = ""
    # Which branch the user picked on the welcome wizard's first fork:
    # "existing" = already running a business, "new" = starting one.
    # Drives whether Adapix acts as a follow-up assistant or a co-founder.
    mode: str = "existing"

    # Facts the owner has explicitly taught Adapix — direct Q&A pairs about
    # THIS business (services, pricing, hours, policies, common questions).
    # Managed from Settings, not the wizard. This is what lets Adapix answer
    # a customer question itself instead of reflexively escalating it: the
    # composing agent quotes these directly, and the inbound classifier is
    # told to treat a question as handle-able ("other") when it's covered
    # here, rather than escalating everything that sounds like a question.
    knowledge_base: list[dict] = field(default_factory=list)

    # Plain-language description of what this business actually DOES, in the
    # owner's own words — the thing a new employee would need to read on
    # day one. Distinct from practice_problems (what's going wrong) and
    # knowledge_base (specific Q&A) — this is the general "who we are and
    # what we offer" context every message and answer should be shaped by.
    description: str = ""

    # Structured services/pricing catalog: [{"id","name","price","details"}].
    # This is what actually lets Adapix answer "what do you offer" and "how
    # much does X cost" with real numbers instead of punting every pricing
    # question to a human.
    services: list[dict] = field(default_factory=list)

    configured_at: str = ""

    # ------------------------------------------------------------------
    # Prompt-fragment builders
    # ------------------------------------------------------------------
    def tone_guidance(self) -> str:
        return TONE_GUIDANCE.get(self.tone, TONE_GUIDANCE["warm_professional"])

    def workflow_prompt_fragment(self) -> str:
        """Plain-language description of every workflow this practice has
        enabled. Pasted into the agent's system prompt so Claude knows what
        Adapix is *supposed* to be doing for this practice."""
        lines = ["This business has Adapix configured to handle:"]
        for wf in self.workflows:
            if wf == "other":
                continue   # custom handled below
            label = WORKFLOW_LABELS.get(wf, wf)
            lines.append(f"  - {label}")
        if self.workflow_custom:
            lines.append(
                f"  - CUSTOM WORKFLOW (in the practice's own words): "
                f"{self.workflow_custom!r}"
            )
            lines.append(
                "    When patients fall into this custom workflow, treat it "
                "as a first-class campaign and apply the same care + escalation "
                "rules you would for the built-in workflows."
            )
        return "\n".join(lines)

    def escalation_prompt_fragment(self) -> str:
        """Description of when the AI should STOP handling and escalate to a
        human. Pasted into both the agent (so it knows when to bail) and the
        classifier (so it knows what to flag)."""
        lines = ["Escalate to a human IMMEDIATELY when any of these occur:"]
        for esc in self.escalations:
            if esc == "other":
                continue
            label = ESCALATION_LABELS.get(esc)
            if label:
                lines.append(f"  - {esc!r}: {label}")
            else:
                lines.append(f"  - {esc!r}: (no description available)")
        if self.escalation_custom:
            lines.append(
                f"  - CUSTOM ESCALATION (in the business's own words): "
                f"{self.escalation_custom!r}"
            )
            lines.append(
                "    Treat this as its own category. When triggered, use the "
                "exact text above as the escalation category name so the "
                "owner sees it labeled the same way they wrote it."
            )
        # emergency is always on regardless of what the user selected
        if "emergency" not in self.escalations:
            lines.append(
                "  - 'emergency': Customer mentions an urgent situation or "
                "safety concern. (ALWAYS ON.)"
            )
        return "\n".join(lines)

    def practice_type_fragment(self) -> str:
        """Description of what KIND of business this is, so Claude's voice,
        question framing, and assumptions match what the business actually
        does. The wizard now uses a searchable picker over ~200 specific
        types (oral surgeon, coffee shop, plumber, etc.), so instead of a
        fixed lookup we just feed the human-readable label straight into
        the prompt and ask Claude to adapt to the conventions of that
        industry."""
        # Prefer the explicit label from the picker; fall back to the slug
        # if an old profile is loaded that pre-dates the label field.
        label = self.practice_type_label.strip()
        if not label:
            label = self.practice_type.replace("_", " ").strip()

        if self.practice_type == "other" and self.practice_type_custom:
            body = (
                f"The owner described their business in their own words as: "
                f"\"{self.practice_type_custom}\". "
                f"Adjust your tone, terminology, and the questions you ask "
                f"to fit this kind of business. Use the conventions of that "
                f"field — the words their customers expect to hear."
            )
        elif label:
            body = (
                f"This is a {label} business. Use the tone, vocabulary, and "
                f"customer expectations typical of that industry. Don't use "
                f"jargon from unrelated fields (medical recall talk for a "
                f"coffee shop, retail bounce-back talk for a law firm, etc.). "
                f"When in doubt, mirror how successful {label}s actually "
                f"talk to their customers."
            )
        else:
            body = "(business type not yet configured)"

        return f"BUSINESS TYPE\n  {body}"

    def problems_fragment(self) -> str:
        """The practice's free-form description of what's slipping through.
        Pasted verbatim into Claude's system prompt so it understands the
        actual pain points it should be working against, not just abstract
        workflow categories."""
        if not (self.practice_problems and self.practice_problems.strip()):
            return ""
        return (
            "THIS PRACTICE'S REAL-WORLD PROBLEMS (in their own words — keep "
            "these top of mind when composing every message):\n"
            f"  \"\"\"\n  {self.practice_problems.strip()}\n  \"\"\""
        )

    def description_fragment(self) -> str:
        """What this business actually does, in the owner's own words —
        general context that shapes every message and answer, distinct from
        the specific Q&A in knowledge_base."""
        if not (self.description and self.description.strip()):
            return ""
        return (
            "WHAT THIS BUSINESS DOES (in the owner's own words):\n"
            f"  \"\"\"\n  {self.description.strip()}\n  \"\"\""
        )

    def services_fragment(self) -> str:
        """Structured services/pricing catalog. This is the difference
        between Adapix vaguely deflecting a pricing question and actually
        quoting a real number."""
        entries = [e for e in (self.services or []) if (e.get("name") or "").strip()]
        if not entries:
            return ""
        lines = ["SERVICES & PRICING (quote these directly when asked what you offer or charge):"]
        for e in entries:
            name = e["name"].strip()
            price = (e.get("price") or "").strip()
            details = (e.get("details") or "").strip()
            line = f"  - {name}"
            if price:
                line += f": {price}"
            lines.append(line)
            if details:
                lines.append(f"    {details}")
        return "\n".join(lines)

    def knowledge_fragment(self) -> str:
        """Owner-taught facts about this specific business. Pasted into the
        composing agent's system prompt so it can answer from these directly
        instead of escalating a question it actually has the answer to."""
        entries = [e for e in (self.knowledge_base or []) if (e.get("q") or "").strip() and (e.get("a") or "").strip()]
        if not entries:
            return ""
        lines = [
            "BUSINESS KNOWLEDGE (taught by the owner — use this to answer "
            "customer questions directly whenever it applies; don't punt a "
            "question to a human just because it sounds technical if the "
            "answer is right here):"
        ]
        for e in entries:
            lines.append(f"  Q: {e['q'].strip()}\n  A: {e['a'].strip()}")
        return "\n".join(lines)

    def classifier_context_fragment(self) -> str:
        """Compact per-org context for the inbound-message classifier — just
        enough for it to know what kind of business this is and what it
        already has taught answers for, so it stops escalating questions
        the business has explicitly said it can handle itself."""
        bits = []
        label = self.practice_type_label.strip() or self.practice_type.replace("_", " ").strip()
        if label:
            bits.append(f"This message is for {self.practice_name}, a {label} business.")
        if self.description and self.description.strip():
            bits.append(f"What they do: {self.description.strip()}")
        services = [e for e in (self.services or []) if (e.get("name") or "").strip()]
        if services:
            names = "; ".join(
                f"{e['name'].strip()} ({e['price'].strip()})" if (e.get("price") or "").strip() else e["name"].strip()
                for e in services[:25]
            )
            bits.append(
                f"They offer these services with real pricing: {names}. A "
                "question asking what they offer or what something costs "
                "should classify as \"other\" (Adapix can quote this "
                "directly) — do not escalate a pricing question just "
                "because it mentions cost."
            )
        entries = [e for e in (self.knowledge_base or []) if (e.get("q") or "").strip() and (e.get("a") or "").strip()]
        if entries:
            sample = "; ".join(e["q"].strip() for e in entries[:25])
            bits.append(
                "The business has taught Adapix direct answers to questions "
                f"like: {sample}. If the customer's message is asking about "
                "one of these (or anything else clearly covered by the "
                "business's own taught knowledge), classify it as \"other\" "
                "so Adapix answers it directly — do not escalate purely "
                "because a message is phrased as a question."
            )
        return " ".join(bits)

    def agent_system_prompt_fragment(self) -> str:
        """Combined fragment for the message-composing agent."""
        parts = [
            f"BUSINESS PROFILE\n"
            f"  Business name: {self.practice_name}\n"
            f"  Owner/Manager: {self.doctor}\n"
            f"  Phone:         {self.phone or '(not configured)'}\n"
            f"  Hours:         {self.hours or '(not configured)'}",
            self.practice_type_fragment(),
            self.tone_guidance(),
            self.workflow_prompt_fragment(),
            self.escalation_prompt_fragment(),
        ]
        desc = self.description_fragment()
        if desc:
            parts.append(desc)
        services = self.services_fragment()
        if services:
            parts.append(services)
        probs = self.problems_fragment()
        if probs:
            parts.append(probs)
        knowledge = self.knowledge_fragment()
        if knowledge:
            parts.append(knowledge)
        # Fold in distilled long-term memory (facts extracted from the
        # /chat conversations and persisted to memory.json). Highest-signal
        # form of the practice's accumulated knowledge — read these as
        # hard rules unless they're tagged 'preference'.
        try:
            from .memory import memory_for_prompt
            mem = memory_for_prompt(max_chars=3000)
            if mem:
                parts.append(mem)
        except Exception:
            pass

        # Also include the raw chat transcript as a fallback for nuance
        # the structured facts may have missed (intent, tone, context).
        try:
            from .chat import chat_transcript_for_prompt
            transcript = chat_transcript_for_prompt(max_chars=3000)
            if transcript:
                parts.append(
                    "RECENT CONVERSATION WITH THIS PRACTICE (raw transcript, "
                    "for tone/context the structured memory above may miss):\n"
                    f"  \"\"\"\n  {transcript}\n  \"\"\""
                )
        except Exception:
            pass
        return "\n\n".join(p for p in parts if p)


# ---------------------------------------------------------------------------
# I/O
# ---------------------------------------------------------------------------
def _profile_path() -> Path:
    var = os.environ.get("ADAPIX_VAR", ".")
    return Path(var) / "practice_profile.json"


def _raw_to_profile(raw: dict) -> PracticeProfile:
    """Convert a wizard JSON payload (from file or DB) into a PracticeProfile."""
    practice = raw.get("practice") or {}
    return PracticeProfile(
        practice_name=practice.get("name") or "your practice",
        doctor=practice.get("owner") or practice.get("doctor") or "there",
        phone=practice.get("phone") or "",
        hours=practice.get("hours") or "",
        tone=raw.get("tone") or "warm_professional",
        workflows=raw.get("workflows") or ["case_acceptance"],
        workflow_custom=raw.get("workflow_custom") or "",
        escalations=raw.get("escalations") or [
            "emergency", "complaint", "callback_request", "pricing_question",
        ],
        escalation_custom=raw.get("escalation_custom") or "",
        practice_problems=raw.get("practice_problems") or "",
        practice_type=raw.get("practice_type") or "",
        practice_type_label=raw.get("practice_type_label") or "",
        practice_type_custom=raw.get("practice_type_custom") or "",
        mode=raw.get("mode") or "existing",
        knowledge_base=raw.get("knowledge_base") or [],
        description=raw.get("description") or "",
        services=raw.get("services") or [],
        configured_at=raw.get("configured_at") or "",
    )


def load_profile(org_id: str | None = None) -> PracticeProfile:
    """Load the practice profile for an org.

    If org_id is given, reads from the org_profiles DB table.
    Falls back to the legacy practice_profile.json flat file for
    backwards-compat with dev environments that haven't signed up yet.
    """
    if org_id:
        try:
            from .db import get_engine
            from .models import OrgProfile
            from sqlalchemy.orm import Session
            with Session(get_engine()) as s:
                row = s.get(OrgProfile, org_id)
                if row and row.data:
                    return _raw_to_profile(row.data)
        except Exception:
            pass
        return PracticeProfile()

    # Legacy flat-file path (dev / pre-signup)
    p = _profile_path()
    if not p.exists():
        return PracticeProfile()
    try:
        raw = json.loads(p.read_text())
    except Exception:
        return PracticeProfile()
    return _raw_to_profile(raw)
