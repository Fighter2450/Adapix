"""Campaign runner.

Orchestrates the agent + channels for active campaigns. Two operations:
  1. start_campaigns_for_eligible_patients() - find eligible patients, create
     a Campaign row for each one that doesn't already have one running.
  2. run_due_messages() - for each active campaign, compose any cadence steps
     whose day has come.

Whether composed messages are SENT or QUEUED FOR APPROVAL depends on the
practice config:
  - approval_mode="auto"     -> compose + send immediately
  - approval_mode="required" -> compose + store with status="pending_approval";
                                 a human approves them via the admin UI or CLI

Idempotent: already-completed steps are not redone.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any

from .agent import AdapixAgent

log = logging.getLogger("adapix.campaign")
from .channels import EmailChannel, SmsChannel
from .config import PracticeConfig, Settings, WorkflowConfig, load_practice, load_workflow
from .db import get_session
from .models import Campaign, CampaignStatus, Message, Patient


PENDING_APPROVAL = "pending_approval"


class CampaignRunner:
    def __init__(self, practice_id: str, workflow_id: str, *, dry_run: bool = False):
        self.practice_id = practice_id
        self.workflow_id = workflow_id
        self.dry_run = dry_run
        self.settings = Settings()
        self.workflow: WorkflowConfig = load_workflow(workflow_id)
        self.practice: PracticeConfig = load_practice(practice_id)
        self.agent = AdapixAgent(self.workflow, self.practice, self.settings)
        self.sms = SmsChannel(self.settings, dry_run=dry_run)
        self.email = EmailChannel(self.settings, dry_run=dry_run)

    # ------------------------------------------------------------------
    # Start campaigns for eligible patients
    # ------------------------------------------------------------------

    def start_campaigns_for_eligible_patients(self) -> int:
        target_status = self.workflow.target.get("patient_status", "consulted_not_started")
        started = 0
        with get_session(self.settings) as s:
            patients = (
                s.query(Patient)
                .filter(
                    Patient.practice_id == self.practice_id,
                    Patient.status == target_status,
                )
                .all()
            )
            for p in patients:
                already = (
                    s.query(Campaign)
                    .filter(
                        Campaign.patient_id == p.id,
                        Campaign.workflow_id == self.workflow.id,
                        Campaign.status == CampaignStatus.active.value,
                    )
                    .first()
                )
                if already:
                    continue
                s.add(
                    Campaign(
                        practice_id=self.practice_id,
                        workflow_id=self.workflow.id,
                        patient_id=p.id,
                    )
                )
                started += 1
        return started

    # ------------------------------------------------------------------
    # Run any due messages
    # ------------------------------------------------------------------

    def run_due_messages(self) -> int:
        sent = 0
        max_day = max((step.day for step in self.workflow.cadence), default=0)

        # Owner-set follow-up rules (Settings → Follow-up rules). Empty dict
        # for legacy YAML practices — every gate below then no-ops.
        try:
            from .practice import load_profile
            rules = load_profile(self.practice_id).rules or {}
        except Exception:
            rules = {}
        first_followup_days = int(rules.get("first_followup_days") or 0)
        max_touches = int(rules.get("max_touches") or 0)

        with get_session(self.settings) as s:
            campaigns = (
                s.query(Campaign)
                .filter(
                    Campaign.practice_id == self.practice_id,
                    Campaign.workflow_id == self.workflow.id,
                    Campaign.status == CampaignStatus.active.value,
                )
                .all()
            )
            now = datetime.utcnow()
            for c in campaigns:
                days_since_start = (now - c.started_at).days

                # Rule: don't start following up until N days after the
                # campaign begins (owner's "first follow-up after" setting).
                if c.last_step_completed == 0 and days_since_start < first_followup_days:
                    continue

                # Rule: stop after N outbound messages with no reply.
                # A reply resets the counter naturally (we count outbound
                # SINCE the most recent inbound).
                if max_touches > 0 and self._outbound_since_last_reply(s, c.id) >= max_touches:
                    continue

                first_day = min((st.day for st in self.workflow.cadence), default=0)
                for step in self.workflow.cadence:
                    # The opening touch drafts immediately (day 0) — a brand-new
                    # user shouldn't wait until tomorrow to see Adapix do
                    # anything. The owner's first_followup_days gate above
                    # still delays it when they've set one.
                    is_first_touch = step.day == first_day and c.last_step_completed == 0
                    if step.day > days_since_start and not is_first_touch:
                        continue
                    if step.day <= c.last_step_completed:
                        continue
                    patient = s.get(Patient, c.patient_id)
                    if patient is None:
                        continue
                    try:
                        self._compose_step_with_retry(s, c, step, patient)
                        c.last_step_completed = step.day
                        sent += 1
                    except Exception as exc:
                        log.warning(f"Skipping step day={step.day} for campaign {c.id}: {exc}")
                if c.last_step_completed >= max_day and max_day > 0:
                    c.status = CampaignStatus.completed.value
        return sent

    @staticmethod
    def _outbound_since_last_reply(session, campaign_id: int) -> int:
        """Outbound messages composed since the contact last replied —
        the counter behind the 're-contact window' rule."""
        msgs = (
            session.query(Message)
            .filter(Message.campaign_id == campaign_id)
            .order_by(Message.created_at.asc())
            .all()
        )
        count = 0
        for m in msgs:
            if m.direction == "inbound":
                count = 0
            elif m.direction == "outbound" and m.status != "rejected":
                count += 1
        return count

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _compose_step_with_retry(self, session, campaign: Campaign, step, patient: Patient, *, retries: int = 3) -> None:
        delay = 5
        for attempt in range(retries):
            try:
                return self._compose_step(session, campaign, step, patient)
            except Exception as exc:
                is_overload = "529" in str(exc) or "overloaded" in str(exc).lower()
                if is_overload and attempt < retries - 1:
                    log.info(f"Claude overloaded, retrying in {delay}s (attempt {attempt+1}/{retries})")
                    time.sleep(delay)
                    delay *= 2
                else:
                    raise

    def _compose_step(self, session, campaign: Campaign, step, patient: Patient) -> None:
        history = self._conversation_history(session, campaign)
        ctx = self._patient_context(patient)
        plan = self.agent.compose_message(
            day=step.day,
            channel=step.channel,
            intent=step.intent,
            patient_context=ctx,
            conversation_history=history,
        )

        approval_required = (self.practice.approval_mode or "auto").lower() == "required"

        if approval_required:
            # Queue for human review - DO NOT send
            session.add(
                Message(
                    campaign_id=campaign.id,
                    direction="outbound",
                    channel=step.channel,
                    day_in_campaign=step.day,
                    subject=plan.subject,
                    body=plan.body,
                    status=PENDING_APPROVAL,
                    metadata_json={"queued_for_approval": True, "intent": step.intent},
                )
            )
            return

        # Auto mode - send immediately
        if step.channel == "sms":
            result = self.sms.send(patient.phone or "", plan.body)
            extra: dict[str, Any] = {"intent": step.intent}
        elif step.channel == "email":
            subject = plan.subject or f"A note from {self.practice.name}"
            result = self.email.send(patient.email or "", subject, plan.body)
            extra = {"intent": step.intent, "subject_planned": plan.subject}
        else:
            raise ValueError(f"Unsupported channel: {step.channel}")

        session.add(
            Message(
                campaign_id=campaign.id,
                direction="outbound",
                channel=step.channel,
                day_in_campaign=step.day,
                subject=plan.subject,
                body=plan.body,
                status=result.status if not result.status.startswith("skipped") else "sent",
                provider_id=result.provider_id,
                metadata_json={**extra, "error": result.error} if result.error else extra,
            )
        )

    @staticmethod
    def _patient_context(p: Patient) -> str:
        parts = [f"Patient: {p.first_name} {p.last_name}"]
        if p.parent_first_name:
            parts.append(
                f"Parent: {p.parent_first_name} {p.parent_last_name or ''}".strip()
            )
        if p.consult_date:
            parts.append(f"Consult date: {p.consult_date.date()}")
        if p.treatment_type:
            parts.append(f"Recommended treatment: {p.treatment_type}")
        if p.treatment_plan_amount is not None:
            parts.append(f"Treatment plan amount: ${p.treatment_plan_amount:,.2f}")
        if p.preferred_channel:
            parts.append(f"Preferred channel: {p.preferred_channel}")
        if p.notes:
            parts.append(f"Notes from consult: {p.notes}")
        return "\n".join(parts)

    @staticmethod
    def _conversation_history(session, campaign: Campaign) -> list[dict[str, Any]]:
        prior = (
            session.query(Message)
            .filter(Message.campaign_id == campaign.id)
            .filter(Message.status.in_(("sent", "received", "delivered", "replied")))
            .order_by(Message.created_at.asc())
            .all()
        )
        history: list[dict[str, Any]] = []
        for m in prior:
            role = "assistant" if m.direction == "outbound" else "user"
            content = m.body if m.channel == "sms" else (
                f"Subject: {m.subject}\n\n{m.body}" if m.subject else m.body
            )
            history.append({"role": role, "content": content})
        return history
