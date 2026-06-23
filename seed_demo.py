"""Adapix demo-data seeder.

Loads 5 realistic OMS patient scenarios into the local SQLite DB so the
surgeon mobile UI at /app shows real-feeling content instead of zeros.

Scenarios:
  1. Wisdom-teeth ghost     - hasn't responded in 3 days; outbound pending approval
  2. Implant cost question  - patient asked pricing; escalated for human (pricing q)
  3. Post-op pain emergency - "pain 8/10, swollen" -> RED escalation
  4. Biopsy follow-up booked - scheduled for Thursday 9:30am
  5. Orthognathic referral  - new referral; outbound pending approval

Run:
    python seed_demo.py            # add demo data (won't duplicate)
    python seed_demo.py --reset    # wipe existing demo data, then re-seed

After running, open http://localhost:8000/app to see the surgeon UI populated.
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

# Make `import adapix` work whether run from project root or elsewhere
SRC = Path(__file__).parent / "src"
if SRC.exists() and str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

# Set a default API key so config doesn't reject load (we don't actually call the API)
os.environ.setdefault("ANTHROPIC_API_KEY", "sk-ant-demo-no-real-calls")

from adapix.db import get_session, init_db
from adapix.models import (
    Campaign,
    CampaignStatus,
    EscalationEvent,
    Message,
    Patient,
    PatientStatus,
)


PRACTICE = "steel-city-oms-demo"
NOW = datetime.utcnow()


def reset_demo_data() -> int:
    """Delete any rows tied to the demo practice. Returns count removed."""
    deleted = 0
    with get_session() as s:
        patients = s.query(Patient).filter(Patient.practice_id == PRACTICE).all()
        for p in patients:
            # Cascade through campaigns -> messages, escalations
            for camp in list(p.campaigns):
                s.query(EscalationEvent).filter(
                    EscalationEvent.campaign_id == camp.id
                ).delete(synchronize_session=False)
                s.query(Message).filter(
                    Message.campaign_id == camp.id
                ).delete(synchronize_session=False)
                s.delete(camp)
                deleted += 1
            s.delete(p)
            deleted += 1
        s.commit()
    return deleted


def _make_patient(
    s,
    first: str,
    last: str,
    phone_last4: str,
    treatment_type: str,
    plan_amount: float | None = None,
    days_since_consult: int | None = None,
    status: PatientStatus = PatientStatus.consulted_not_started,
    notes: str | None = None,
) -> Patient:
    p = Patient(
        practice_id=PRACTICE,
        first_name=first,
        last_name=last,
        phone=f"+1412555{phone_last4}",
        email=f"{first.lower()}.{last.lower()}@example.com",
        preferred_channel="sms",
        consult_date=(
            NOW - timedelta(days=days_since_consult)
            if days_since_consult is not None else None
        ),
        treatment_type=treatment_type,
        treatment_plan_amount=plan_amount,
        notes=notes,
        status=status.value,
        created_at=NOW - timedelta(days=days_since_consult or 0),
    )
    s.add(p)
    s.flush()
    return p


def _make_campaign(
    s, patient: Patient, workflow_id: str, started_days_ago: int = 0,
    last_step: int = 0,
    status: CampaignStatus = CampaignStatus.active,
) -> Campaign:
    c = Campaign(
        practice_id=PRACTICE,
        workflow_id=workflow_id,
        patient_id=patient.id,
        started_at=NOW - timedelta(days=started_days_ago),
        last_step_completed=last_step,
        status=status.value,
    )
    s.add(c)
    s.flush()
    return c


def _make_message(
    s, campaign: Campaign, *, direction: str, channel: str, body: str,
    status: str = "sent", day_in_campaign: int | None = None,
    minutes_ago: int = 60, subject: str | None = None,
) -> Message:
    m = Message(
        campaign_id=campaign.id,
        direction=direction,
        channel=channel,
        day_in_campaign=day_in_campaign,
        subject=subject,
        body=body,
        status=status,
        created_at=NOW - timedelta(minutes=minutes_ago),
    )
    s.add(m)
    s.flush()
    return m


def _make_escalation(
    s, campaign: Campaign, *, category: str, confidence: str,
    reasoning: str, suggested_action: str,
    triggered_by_message_id: int | None = None,
    minutes_ago: int = 30,
) -> EscalationEvent:
    e = EscalationEvent(
        campaign_id=campaign.id,
        triggered_by_message_id=triggered_by_message_id,
        category=category,
        confidence=confidence,
        reasoning=reasoning,
        suggested_action=suggested_action,
        resolved=False,
        created_at=NOW - timedelta(minutes=minutes_ago),
    )
    s.add(e)
    s.flush()
    return e


def seed():
    """Insert the 5 scenarios."""
    counts = {"patients": 0, "campaigns": 0, "messages": 0, "escalations": 0}

    with get_session() as s:
        # Skip if already seeded (idempotent)
        existing = s.query(Patient).filter(Patient.practice_id == PRACTICE).count()
        if existing > 0:
            print(f"[seed_demo] Found {existing} demo patients already. Skipping.")
            print(f"           Run with --reset to wipe and re-seed.")
            return counts

        # ----- 1. Wisdom-teeth ghost (Maria) -----
        maria = _make_patient(
            s, "Maria", "Lopez", "4421",
            treatment_type="wisdom teeth extraction (all 4)",
            plan_amount=3200.0,
            days_since_consult=3,
            notes="Referred by Dr. Allen (GP). Hasn't responded to 2 contacts.",
        )
        maria_c = _make_campaign(s, maria, "case_acceptance", started_days_ago=3, last_step=2)
        # Day 1 outbound (sent)
        _make_message(s, maria_c, direction="outbound", channel="sms",
                      day_in_campaign=1, minutes_ago=3*24*60,
                      body=("Hi Maria, this is Dr. Patel's office at Steel City Oral Surgery. "
                            "Just confirming you got Dr. Allen's referral for your wisdom teeth. "
                            "Can I help find a time that works for the consult?"))
        # Day 3 outbound DRAFT awaiting approval
        _make_message(s, maria_c, direction="outbound", channel="sms",
                      day_in_campaign=3, minutes_ago=8, status="pending_approval",
                      body=("Hi Maria - Dr. Patel held a 9:30a Thursday for your wisdom-teeth "
                            "consult. Want me to lock it in? Reply YES and I'll send the "
                            "address + paperwork link."))

        # ----- 2. Implant cost question (Jorge) -----
        jorge = _make_patient(
            s, "Jorge", "Ramirez", "7780",
            treatment_type="full upper arch implant (4-on-1)",
            plan_amount=27500.0,
            days_since_consult=5,
            notes="Insurance: BCBS PPO. Asked about financing on initial call.",
        )
        jorge_c = _make_campaign(s, jorge, "case_acceptance", started_days_ago=5, last_step=3)
        _make_message(s, jorge_c, direction="outbound", channel="sms",
                      day_in_campaign=1, minutes_ago=5*24*60,
                      body=("Hi Jorge, this is Dr. Patel's office. Reaching out about your "
                            "implant consult. Most patients ask about cost & financing first - "
                            "happy to share details whenever you're ready."))
        jorge_inbound = _make_message(s, jorge_c, direction="inbound", channel="sms",
                                      day_in_campaign=3, minutes_ago=12,
                                      body="what's the cost for everything?")
        _make_escalation(s, jorge_c,
                         category="callback_request",
                         confidence="high",
                         reasoning=("Patient is asking about pricing. Per practice policy "
                                    "(see config), pricing conversations must be handled "
                                    "by office staff with full insurance context."),
                         suggested_action="Have Karen call Jorge today before 5pm to walk through cost + CareCredit options.",
                         triggered_by_message_id=jorge_inbound.id,
                         minutes_ago=11)

        # ----- 3. Post-op pain emergency (Lina) -----
        lina = _make_patient(
            s, "Lina", "Kim", "2210",
            treatment_type="impacted #17 + #32 extraction",
            plan_amount=2800.0,
            days_since_consult=10,
            status=PatientStatus.treatment_started,
            notes="Extraction performed 3 days ago. Rx: hydrocodone 5/325 qid PRN.",
        )
        lina_c = _make_campaign(s, lina, "post_op_check_in",
                                started_days_ago=3, last_step=2)
        _make_message(s, lina_c, direction="outbound", channel="sms",
                      day_in_campaign=2, minutes_ago=24*60,
                      body=("Hi Lina, day 2 check-in from Dr. Patel's office. How is your "
                            "recovery going? Any pain, swelling, or bleeding to mention?"))
        lina_inbound = _make_message(s, lina_c, direction="inbound", channel="sms",
                                     day_in_campaign=3, minutes_ago=2,
                                     body=("Pain is 8/10 since last night and the right side "
                                           "is really swollen. The meds aren't doing much "
                                           "anymore. should I be worried?"))
        _make_escalation(s, lina_c,
                         category="emergency",
                         confidence="high",
                         reasoning=("Patient reports pain escalation to 8/10 and significant "
                                    "swelling on post-op day 3. Possible dry socket or "
                                    "infection. Falls outside scope of automated reassurance."),
                         suggested_action="Page Dr. Patel NOW. Same-day visit or telehealth eval required.",
                         triggered_by_message_id=lina_inbound.id,
                         minutes_ago=1)

        # ----- 4. Biopsy follow-up scheduled (David) -----
        david = _make_patient(
            s, "David", "Chen", "9912",
            treatment_type="biopsy follow-up — leukoplakia, lateral tongue",
            plan_amount=None,
            days_since_consult=14,
            status=PatientStatus.treatment_started,
            notes="Biopsy benign per pathology report. Confirming 6-mo recall scheduled.",
        )
        david_c = _make_campaign(s, david, "recall_6mo",
                                 started_days_ago=2, last_step=2,
                                 status=CampaignStatus.completed)
        _make_message(s, david_c, direction="outbound", channel="sms",
                      day_in_campaign=1, minutes_ago=22*60,
                      body=("Hi David, Dr. Patel's office. Your biopsy results are back and "
                            "the news is good - benign. We'd like to see you back at the "
                            "6-month mark just to recheck. Open Thursday May 21 at 9:30a or "
                            "Mon May 25 at 11a?"))
        _make_message(s, david_c, direction="inbound", channel="sms",
                      day_in_campaign=1, minutes_ago=21*60,
                      body="Thursday 9:30 works. Thanks for the good news.")
        _make_message(s, david_c, direction="outbound", channel="sms",
                      day_in_campaign=1, minutes_ago=20*60, status="delivered",
                      body=("Locked in - Thursday May 21 at 9:30a. Confirmation + address "
                            "coming to your email. See you then."))

        # ----- 5. Orthognathic referral (Sarah) - new lead -----
        sarah = _make_patient(
            s, "Sarah", "Williams", "0356",
            treatment_type="orthognathic consult (Class III, ortho-surgical)",
            plan_amount=None,
            days_since_consult=1,
            notes="Referred by Dr. Khan (orthodontist). Existing ortho treatment 14 mo.",
        )
        sarah_c = _make_campaign(s, sarah, "case_acceptance", started_days_ago=1, last_step=0)
        _make_message(s, sarah_c, direction="outbound", channel="sms",
                      day_in_campaign=1, minutes_ago=25, status="pending_approval",
                      body=("Hi Sarah, this is Dr. Patel's office at Steel City Oral Surgery. "
                            "Dr. Khan sent over your records for the orthognathic consult. "
                            "I can offer Tuesday May 19 at 2p or Friday May 22 at 10a - "
                            "either work?"))

        s.commit()

        # Tally
        counts["patients"] = s.query(Patient).filter(Patient.practice_id == PRACTICE).count()
        counts["campaigns"] = s.query(Campaign).filter(Campaign.practice_id == PRACTICE).count()
        counts["messages"] = (
            s.query(Message).join(Campaign).filter(Campaign.practice_id == PRACTICE).count()
        )
        counts["escalations"] = (
            s.query(EscalationEvent).join(Campaign).filter(Campaign.practice_id == PRACTICE).count()
        )

    return counts


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--reset", action="store_true",
                    help="wipe existing demo data before seeding")
    args = ap.parse_args()

    init_db()

    if args.reset:
        n = reset_demo_data()
        print(f"[seed_demo] Removed {n} existing demo rows.")

    counts = seed()
    if any(counts.values()):
        print(f"[seed_demo] Seeded: "
              f"{counts['patients']} patients, "
              f"{counts['campaigns']} campaigns, "
              f"{counts['messages']} messages, "
              f"{counts['escalations']} escalations.")
        print("[seed_demo] Open http://localhost:8000/app to see the surgeon UI.")
    else:
        print("[seed_demo] No new data added.")


if __name__ == "__main__":
    main()
