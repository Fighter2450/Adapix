"""Inbound message processor.

When a patient/parent replies (currently SMS only), this module:
  1. Looks up the patient by phone, finds the active campaign.
  2. Logs the inbound message.
  3. Classifies it via the Escalator.
  4. Dispatches the right action:
       - stop / decline       → close the campaign, send (or don't) acknowledgment
       - clinical_question    → escalate to doctor, send acknowledgment
       - callback_request     → escalate to office manager, send acknowledgment
       - emergency            → urgent escalation, redirect to office phone
       - other (continue)     → agent composes a normal SMS reply

Dispatch results are returned so the webhook handler can log + respond.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .agent import AdapixAgent
from .channels import SmsChannel
from .config import Settings, load_practice, load_workflow
from .db import get_session
from .escalation import Classification, Escalator
from .models import (
    Campaign,
    CampaignStatus,
    EscalationEvent,
    Message,
    Patient,
    PatientStatus,
)


@dataclass
class InboundResult:
    status: str                        # responded | escalated | declined | stopped | emergency | ignored
    classification: Classification | None = None
    response_body: str | None = None
    reason: str | None = None


class InboundProcessor:
    """Handles inbound replies. Construct once, call .process_sms() per inbound."""

    def __init__(self, *, dry_run: bool = False):
        self.settings = Settings()
        self.dry_run = dry_run
        self.escalator = Escalator(self.settings)
        self.sms = SmsChannel(self.settings, dry_run=dry_run)

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    def process_sms(
        self,
        from_number: str,
        body: str,
        provider_id: str | None = None,
        to_number: str | None = None,
    ) -> InboundResult:
        with get_session(self.settings) as s:
            # Tenant isolation: the number the customer texted identifies the
            # business. Without this, two orgs sharing a contact's phone would
            # leak each other's replies.
            from .phone import normalize_phone
            q = s.query(Patient).filter(Patient.phone == (normalize_phone(from_number) or from_number))
            if to_number:
                from .models import Organization
                # The customer may have texted either the org's calling
                # number (Twilio inbound) or its dedicated iMessage line
                # (Blooio inbound) — both identify the same business.
                org = (
                    s.query(Organization)
                    .filter((Organization.phone_number == to_number)
                            | (Organization.imessage_number == to_number))
                    .first()
                )
                if org is not None:
                    q = q.filter(Patient.practice_id == org.id)
            patient = q.first()
            if patient is None:
                return InboundResult(status="ignored", reason="no patient match for phone")

            campaign = (
                s.query(Campaign)
                .filter(
                    Campaign.patient_id == patient.id,
                    Campaign.status == CampaignStatus.active.value,
                    # Ad-hoc call plans and review requests create bookkeeping
                    # campaigns with no loadable workflow — an SMS reply must
                    # never route there.
                    Campaign.workflow_id.notin_(("voice_call", "review_request", "missed_call_textback")),
                )
                .order_by(Campaign.started_at.desc())
                .first()
            )
            if campaign is None:
                # Still log inbound for audit, but no action
                s.add(
                    Message(
                        campaign_id=None,
                        direction="inbound",
                        channel="sms",
                        body=body,
                        status="received_no_campaign",
                        provider_id=provider_id,
                    )
                )
                return InboundResult(status="ignored", reason="no active campaign")

            # Log the inbound message
            inbound = Message(
                campaign_id=campaign.id,
                direction="inbound",
                channel="sms",
                body=body,
                status="received",
                provider_id=provider_id,
            )
            s.add(inbound)
            s.flush()  # so inbound.id is populated

            # What did the customer just reveal about THEMSELVES, distinct
            # from routing the message (below) — runs regardless of
            # escalation category, since even an escalated question can
            # carry a fact worth keeping ("the noise is bothering my baby").
            try:
                from .patient_memory import remember_from_message
                remember_from_message(patient, body)
            except Exception:
                pass

            history = self._conversation_history(s, campaign.id, exclude_message_id=inbound.id)
            from .practice import load_profile
            profile = load_profile(patient.practice_id)
            classification = self.escalator.classify(
                body, history, business_context=profile.classifier_context_fragment()
            )

            # Log escalation event for any non-"other" category
            if classification.category != "other":
                s.add(
                    EscalationEvent(
                        campaign_id=campaign.id,
                        triggered_by_message_id=inbound.id,
                        category=classification.category,
                        confidence=classification.confidence,
                        reasoning=classification.reasoning,
                        suggested_action=classification.suggested_action,
                    )
                )
                try:
                    from .notifications import push_notification
                    who = f"{patient.first_name} {patient.last_name}".strip() or "A customer"
                    cat = (classification.category or "needs you").replace("_", " ")
                    push_notification(
                        title=f"{who} needs you",
                        body=f"Flagged: {cat}. The full conversation is in your Inbox.",
                        url="/app", tag="adapix-escalation", org_id=patient.practice_id,
                    )
                except Exception:
                    pass

            # Dispatch
            return self._dispatch(s, campaign, patient, classification, history, body)

    # ------------------------------------------------------------------
    # Call outcomes (from the Vapi end-of-call-report webhook)
    # ------------------------------------------------------------------

    def process_call_outcome(
        self,
        *,
        transcript: str,
        summary: str = "",
        ended_reason: str = "",
        patient_id: int | None = None,
        campaign_id: int | None = None,
        from_number: str | None = None,
        provider_id: str | None = None,
        recording_url: str | None = None,
    ) -> InboundResult:
        """Store a finished AI call's transcript, classify what happened, and
        raise an escalation into the Inbox when it needs a human — the voice
        analog of process_sms(). Contact/campaign come from the call metadata
        we attached when placing it, falling back to the customer's number."""
        text = (summary or transcript or "").strip()
        with get_session(self.settings) as s:
            patient = None
            if patient_id:
                patient = s.get(Patient, int(patient_id))
            if patient is None and from_number:
                from .phone import normalize_phone
                patient = s.query(Patient).filter(Patient.phone == (normalize_phone(from_number) or from_number)).first()
            if patient is None:
                return InboundResult(status="ignored", reason="no contact match for call")

            campaign = None
            if campaign_id:
                campaign = s.get(Campaign, int(campaign_id))
            if campaign is None:
                campaign = (
                    s.query(Campaign)
                    .filter(Campaign.patient_id == patient.id,
                            Campaign.workflow_id != "voice_call")
                    .order_by(Campaign.started_at.desc())
                    .first()
                ) or (
                    s.query(Campaign)
                    .filter(Campaign.patient_id == patient.id)
                    .order_by(Campaign.started_at.desc())
                    .first()
                )

            # Log the call transcript as an inbound record on the contact.
            rec = Message(
                campaign_id=campaign.id if campaign else None,
                direction="inbound",
                channel="call",
                subject=(summary[:250] or None) if summary else None,
                body=(transcript or summary or "(call ended — no transcript)"),
                status="received",
                provider_id=provider_id,
                metadata_json={
                    "kind": "call_outcome",
                    "ended_reason": ended_reason,
                    **({"recording_url": recording_url} if recording_url else {}),
                },
            )
            s.add(rec)
            s.flush()

            # A full call transcript usually carries much more about the
            # customer than a text ever would — same per-contact memory as
            # process_sms().
            try:
                from .patient_memory import remember_from_message
                if text.strip():
                    remember_from_message(patient, text)
            except Exception:
                pass

            # Classify what happened on the call (same engine as inbound SMS).
            from .practice import load_profile
            profile = load_profile(patient.practice_id)
            classification = self.escalator.classify(
                text or "(no transcript)", [],
                business_context=profile.classifier_context_fragment(),
            )

            if campaign and classification.category != "other":
                s.add(
                    EscalationEvent(
                        campaign_id=campaign.id,
                        triggered_by_message_id=rec.id,
                        category=classification.category,
                        confidence=classification.confidence,
                        reasoning=classification.reasoning,
                        suggested_action=classification.suggested_action,
                    )
                )
                try:
                    from .notifications import push_notification
                    who = f"{patient.first_name} {patient.last_name}".strip() or "A customer"
                    cat = (classification.category or "needs you").replace("_", " ")
                    push_notification(
                        title=f"{who} needs you",
                        body=f"Flagged after a call: {cat}. Recording + transcript in your Inbox.",
                        url="/app", tag="adapix-escalation", org_id=patient.practice_id,
                    )
                except Exception:
                    pass
                return InboundResult(status="escalated", classification=classification)

            return InboundResult(status="logged", classification=classification)

    @staticmethod
    def _opt_out_patient(session, patient: Patient, status: str | None = None) -> None:
        """Patient-level, cross-channel opt-out: flag the contact, stop every
        active campaign, and reject every draft still waiting for approval so
        nothing to this person is one tap from sending."""
        from datetime import datetime as _dt
        patient.opted_out = True
        patient.opted_out_at = _dt.utcnow()
        if status:
            patient.status = status
        elif patient.status == PatientStatus.consulted_not_started.value:
            patient.status = PatientStatus.paused.value
        campaigns = (
            session.query(Campaign)
            .filter(Campaign.patient_id == patient.id)
            .all()
        )
        for c in campaigns:
            if c.status == CampaignStatus.active.value:
                c.status = CampaignStatus.stopped.value
            for m in (
                session.query(Message)
                .filter(Message.campaign_id == c.id, Message.status.in_(("pending_approval", "approved")))
                .all()
            ):
                m.status = "rejected"
                meta = dict(m.metadata_json or {})
                meta["rejected_reason"] = "contact opted out"
                m.metadata_json = meta

    # ------------------------------------------------------------------
    # Dispatch by classification
    # ------------------------------------------------------------------

    def _dispatch(
        self,
        session,
        campaign: Campaign,
        patient: Patient,
        classification: Classification,
        history: list[dict[str, Any]],
        inbound_body: str,
    ) -> InboundResult:
        cat = classification.category

        if cat == "stop":
            campaign.status = CampaignStatus.stopped.value
            # TCPA: opt-out is PATIENT-level and cross-channel, not one
            # campaign row. Close everything, kill every pending draft.
            self._opt_out_patient(session, patient)
            return InboundResult(status="stopped", classification=classification)

        if cat == "decline":
            campaign.status = CampaignStatus.declined.value
            # An explicit "no" ends all outreach too — the one-time polite
            # acknowledgment below is the last thing they hear from us.
            self._opt_out_patient(session, patient, status=PatientStatus.explicitly_declined.value)
            body = self._decline_acknowledgment(campaign.practice_id)
            self._send_and_log(session, campaign, patient, body, "decline_ack")
            return InboundResult(
                status="declined",
                classification=classification,
                response_body=body,
            )

        if cat == "emergency":
            campaign.status = CampaignStatus.escalated.value
            body = self._emergency_redirect(campaign.practice_id)
            self._send_and_log(session, campaign, patient, body, "emergency_redirect")
            return InboundResult(
                status="emergency",
                classification=classification,
                response_body=body,
            )

        if cat in ("clinical_question", "callback_request"):
            campaign.status = CampaignStatus.escalated.value
            body = self._escalation_acknowledgment(campaign.practice_id, cat)
            self._send_and_log(session, campaign, patient, body, f"escalation_ack:{cat}")
            return InboundResult(
                status="escalated",
                classification=classification,
                response_body=body,
            )

        # cat == "other" — let the agent reply
        try:
            workflow = load_workflow(campaign.workflow_id)
        except FileNotFoundError:
            # Unloadable workflow (legacy/ad-hoc campaign): don't crash the
            # webhook — escalate so a human answers instead.
            campaign.status = CampaignStatus.escalated.value
            return InboundResult(status="escalated", classification=classification)
        practice = load_practice(campaign.practice_id)
        agent = AdapixAgent(workflow=workflow, practice=practice, settings=self.settings)
        from .patient_memory import format_memory
        plan = agent.respond_to_inbound(
            inbound_body, history, patient_memory=format_memory(patient.memory_json or [])
        )
        self._send_and_log(session, campaign, patient, plan.body, "agent_reply")
        return InboundResult(
            status="responded",
            classification=classification,
            response_body=plan.body,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _send_and_log(
        self,
        session,
        campaign: Campaign,
        patient: Patient,
        body: str,
        kind: str,
    ) -> None:
        # Same transport order as ApprovalManager._send_one: the org's OWN
        # Blooio line first, then the shared Claw line, then Twilio SMS.
        # This matters most here of anywhere — the customer just texted a
        # specific number, and an answer sent from a DIFFERENT number lands
        # in a different thread on their phone (or not at all). The reply
        # must go back out the same line the question came in on.
        result = None
        md: dict = {"kind": kind}
        from .models import Organization
        org = session.get(Organization, campaign.practice_id)
        if self.settings.prefer_imessage and org is not None and org.blooio_channel_id:
            from .channels import IMessageChannel
            imsg = IMessageChannel(self.settings, dry_run=self.dry_run)
            if imsg.is_configured(org.blooio_channel_id):
                r = imsg.send(patient.phone or "", body, channel_id=org.blooio_channel_id)
                if r.status != "failed":
                    result = r
                    md["transport"] = "imessage"
                else:
                    md["imessage_error"] = r.error
        if result is None:
            from .channels import ClawChannel
            claw = ClawChannel(self.settings, dry_run=self.dry_run)
            if self.settings.prefer_imessage and claw.is_configured():
                r = claw.send(patient.phone or "", body)
                if r.status != "failed":
                    result = r
                    md["transport"] = "imessage-claw"
                else:
                    md["imessage_error"] = r.error
        if result is None:
            result = self.sms.send(patient.phone or "", body)
        if result.error:
            md["error"] = result.error
        session.add(
            Message(
                campaign_id=campaign.id,
                direction="outbound",
                channel="sms",
                body=body,
                status=result.status,
                provider_id=result.provider_id,
                metadata_json=md,
            )
        )

    def _decline_acknowledgment(self, practice_id: str) -> str:
        practice = load_practice(practice_id)
        return (
            f"Totally understand — thank you for letting us know. We'll pause "
            f"the follow-up. If anything changes down the road, you can always "
            f"reach the {practice.name} office at {practice.office_phone}."
        )

    def _emergency_redirect(self, practice_id: str) -> str:
        practice = load_practice(practice_id)
        return (
            f"That sounds urgent — please call our office right now at "
            f"{practice.office_phone}. If after hours and severe, please go to "
            f"the nearest urgent care."
        )

    def _escalation_acknowledgment(self, practice_id: str, category: str) -> str:
        practice = load_practice(practice_id)
        if category == "clinical_question":
            return (
                f"Great question — I want to make sure you get the right answer. "
                f"I'm flagging this for {practice.doctor_name} to follow up with "
                f"you directly. Office hours: {practice.office_hours}."
            )
        return (
            f"Got it — I'll have someone from {practice.name} call you back. "
            f"Our office hours are {practice.office_hours}, and the direct "
            f"line is {practice.office_phone}."
        )

    @staticmethod
    def _conversation_history(
        session,
        campaign_id: int,
        exclude_message_id: int | None = None,
    ) -> list[dict[str, Any]]:
        prior = (
            session.query(Message)
            .filter(Message.campaign_id == campaign_id)
            .order_by(Message.created_at.asc())
            .all()
        )
        history: list[dict[str, Any]] = []
        for m in prior:
            if exclude_message_id is not None and m.id == exclude_message_id:
                continue
            role = "assistant" if m.direction == "outbound" else "user"
            content = (
                f"Subject: {m.subject}\n\n{m.body}"
                if m.subject and m.channel == "email"
                else m.body
            )
            history.append({"role": role, "content": content})
        return history
