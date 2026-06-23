"""Escalation engine.

Classifies an inbound reply into one of a small set of categories so the
inbound processor knows whether to: respond, escalate to the doctor, escalate
to the office manager, stop the campaign, or treat as an emergency.

The classifier is a Claude call with a low max_tokens and structured JSON output.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from anthropic import Anthropic

from .config import Settings


CLASSIFICATION_SYSTEM = """\
You classify a single inbound message from a patient or parent during an
orthodontic follow-up campaign. Choose ONE category that best fits:

- clinical_question — anything that requires a doctor or clinician to answer:
  treatment specifics, side effects, pain, broken appliance behavior, X-rays,
  bite questions, "should I?" medical decisions.
- callback_request — they explicitly ask for a phone call, to talk to someone,
  or to be contacted by the office.
- decline — they indicate they will not proceed with treatment, are going
  elsewhere, or want to be left alone (without using the STOP keyword).
- emergency — they mention pain, swelling, bleeding, infection, severe
  discomfort, or anything that sounds urgent.
- stop — the message is a STOP / UNSUBSCRIBE / END keyword (TCPA opt-out).
- other — a normal conversational reply the AI assistant can handle:
  financing questions, scheduling-information questions, thank yous, general
  questions about the practice, casual replies.

Output ONLY a single JSON object on one line, no markdown, no preamble:
{"category":"<one of the above>","confidence":"high|medium|low","reasoning":"<one short sentence>","suggested_action":"<one short sentence>"}
"""


@dataclass
class Classification:
    category: str
    confidence: str
    reasoning: str
    suggested_action: str

    @classmethod
    def fallback(cls, raw: str) -> "Classification":
        return cls(
            category="other",
            confidence="low",
            reasoning=f"Could not parse classifier output: {raw[:140]}",
            suggested_action="Treat as a general response and let the agent reply.",
        )


class Escalator:
    """Inbound message classifier."""

    def __init__(self, settings: Settings, model: str | None = None):
        self.settings = settings
        self.model = model or settings.adapix_model
        self._client = Anthropic(api_key=settings.anthropic_api_key)

    def classify(
        self,
        body: str,
        history: list[dict[str, Any]] | None = None,
    ) -> Classification:
        # Quick rule-based pre-filter for STOP keywords (TCPA)
        normalized = body.strip().upper()
        if normalized in {"STOP", "STOPALL", "UNSUBSCRIBE", "CANCEL", "END", "QUIT"}:
            return Classification(
                category="stop",
                confidence="high",
                reasoning="Recipient sent an SMS opt-out keyword.",
                suggested_action="Stop the campaign immediately. Do not send any further messages.",
            )

        messages: list[dict[str, Any]] = list(history or [])
        messages.append({"role": "user", "content": body})

        response = self._client.messages.create(
            model=self.model,
            max_tokens=256,
            system=CLASSIFICATION_SYSTEM,
            messages=messages,
        )
        raw = response.content[0].text.strip()
        return self._parse(raw)

    @staticmethod
    def _parse(raw: str) -> Classification:
        # Strip code fences if the model added any
        text = raw.strip()
        if text.startswith("```"):
            text = text.strip("`")
            # Drop a leading "json" tag if present
            if text.lower().startswith("json"):
                text = text[4:].strip()
        try:
            data = json.loads(text)
            return Classification(
                category=str(data.get("category", "other")),
                confidence=str(data.get("confidence", "medium")),
                reasoning=str(data.get("reasoning", "")),
                suggested_action=str(data.get("suggested_action", "")),
            )
        except (json.JSONDecodeError, AttributeError):
            return Classification.fallback(raw)
