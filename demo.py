"""Smoke-test demo: load configs, compose one message, print the result.

Run this once you have a .env with ANTHROPIC_API_KEY set:

    python demo.py

It will compose a Day-1 SMS message for a fictional patient using the
case_acceptance workflow and the example practice config. No SMS is sent —
the message is only printed to stdout.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Make src/ importable when running this from the project root
sys.path.insert(0, str(Path(__file__).parent / "src"))

from adapix.agent import AdapixAgent
from adapix.config import Settings, load_practice, load_workflow


def main() -> None:
    settings = Settings()
    workflow = load_workflow("case_acceptance")
    practice = load_practice("example")
    agent = AdapixAgent(workflow=workflow, practice=practice, settings=settings)

    fictional_patient_context = (
        "Patient name: Lily Hoffman (age 13). Parent: Megan Hoffman.\n"
        "Consult date: 7 days ago. Recommended treatment: clear aligners, ~18 months, $5,800.\n"
        "Concerns Megan raised at the consult: cost (asked twice), whether Lily is mature enough\n"
        "to follow through, and whether they could wait 6-12 months. Megan said she would\n"
        "'talk to her husband and think about it.' No financing application started yet.\n"
        "Communication preference: text first, then email if no response."
    )

    # Find the day-1 step
    step = next(s for s in workflow.cadence if s.day == 1)

    plan = agent.compose_message(
        day=step.day,
        channel=step.channel,
        intent=step.intent,
        patient_context=fictional_patient_context,
    )

    print("=" * 60)
    print(f"Workflow: {workflow.name}")
    print(f"Practice: {practice.name}")
    print(f"Step:     Day {step.day} via {step.channel}")
    print(f"Intent:   {step.intent}")
    print("=" * 60)
    if plan.subject:
        print(f"Subject: {plan.subject}\n")
    print(plan.body)
    print("=" * 60)


if __name__ == "__main__":
    main()
