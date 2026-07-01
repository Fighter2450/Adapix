"""Adapix CLI.

Usage:
    python -m adapix.cli init-db
    python -m adapix.cli list-workflows
    python -m adapix.cli list-practices
    python -m adapix.cli ingest <csv> --practice <practice_id>
    python -m adapix.cli start-campaigns --practice <id> --workflow <id>
    python -m adapix.cli run --practice <id> --workflow <id> [--dry-run]
"""
from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

import typer

from .approval import ApprovalManager
from .campaign import CampaignRunner
from .config import Settings, list_practices, list_workflows, load_practice
from .db import get_session, init_db
from .inbound import InboundProcessor
from .models import Patient

app = typer.Typer(help="Adapix CLI", add_completion=False)


@app.command("init-db")
def cmd_init_db():
    """Create the database tables (idempotent)."""
    init_db(Settings())
    typer.echo("Database initialized.")


@app.command("list-workflows")
def cmd_list_workflows():
    for w in list_workflows():
        typer.echo(w)


@app.command("list-practices")
def cmd_list_practices():
    for p in list_practices():
        typer.echo(p)


@app.command("ingest")
def cmd_ingest(
    csv_path: Path = typer.Argument(..., exists=True, readable=True),
    practice: str = typer.Option(..., help="Practice ID (matches config/practices/<id>.yaml)"),
):
    """Ingest a CSV of consult patients.

    Expected columns (header row required):
      external_id, first_name, last_name, parent_first_name, parent_last_name,
      phone, email, preferred_channel, consult_date (YYYY-MM-DD),
      treatment_type, treatment_plan_amount, notes
    """
    load_practice(practice)  # validate the practice exists
    init_db()
    count = 0
    with csv_path.open(newline="") as f, get_session() as s:
        reader = csv.DictReader(f)
        for row in reader:
            consult_date = None
            if row.get("consult_date"):
                consult_date = datetime.fromisoformat(row["consult_date"])
            amount = None
            if row.get("treatment_plan_amount"):
                try:
                    amount = float(row["treatment_plan_amount"])
                except ValueError:
                    amount = None
            s.add(
                Patient(
                    practice_id=practice,
                    external_id=row.get("external_id") or None,
                    first_name=row.get("first_name", "").strip(),
                    last_name=row.get("last_name", "").strip(),
                    parent_first_name=(row.get("parent_first_name") or "").strip() or None,
                    parent_last_name=(row.get("parent_last_name") or "").strip() or None,
                    phone=(row.get("phone") or "").strip() or None,
                    email=(row.get("email") or "").strip() or None,
                    preferred_channel=(row.get("preferred_channel") or "sms").strip(),
                    consult_date=consult_date,
                    treatment_type=(row.get("treatment_type") or "").strip() or None,
                    treatment_plan_amount=amount,
                    notes=(row.get("notes") or "").strip() or None,
                )
            )
            count += 1
    typer.echo(f"Ingested {count} patients into practice '{practice}'.")


@app.command("start-campaigns")
def cmd_start_campaigns(
    practice: str = typer.Option(...),
    workflow: str = typer.Option("case_acceptance"),
):
    """Start a campaign for every eligible patient who doesn't already have one."""
    runner = CampaignRunner(practice, workflow)
    n = runner.start_campaigns_for_eligible_patients()
    typer.echo(f"Started {n} new campaign(s).")


@app.command("run")
def cmd_run(
    practice: str = typer.Option(...),
    workflow: str = typer.Option("case_acceptance"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Compose + log but don't actually send"),
):
    """Send any cadence steps that are due across active campaigns."""
    runner = CampaignRunner(practice, workflow, dry_run=dry_run)
    n = runner.run_due_messages()
    typer.echo(f"Processed {n} message(s).")


@app.command("pending-approvals")
def cmd_pending_approvals(
    practice: str = typer.Option(None, help="Filter to one practice (optional)"),
):
    """List messages waiting for human approval."""
    mgr = ApprovalManager()
    items = mgr.list_pending(practice_id=practice)
    typer.echo(f"{len(items)} message(s) pending approval")
    for m in items:
        typer.echo("")
        typer.echo(f"#{m.id}  practice={m.practice_id}  patient={m.patient_name}")
        typer.echo(f"     channel={m.channel}  day={m.day_in_campaign or '-'}  to={m.patient_phone or m.patient_email or '?'}")
        if m.subject:
            typer.echo(f"     Subject: {m.subject}")
        body_preview = m.body if len(m.body) <= 200 else m.body[:200] + "..."
        typer.echo(f"     Body:    {body_preview}")


@app.command("approve")
def cmd_approve(
    message_id: int = typer.Argument(...),
    send: bool = typer.Option(True, "--send/--queue", help="Send immediately or just mark approved"),
    dry_run: bool = typer.Option(False, "--dry-run/--live"),
):
    """Approve a pending message by id."""
    mgr = ApprovalManager(dry_run=dry_run)
    if send:
        result = mgr.approve_and_send(message_id)
        typer.echo(f"Message #{message_id}: {result}")
    else:
        ok = mgr.approve(message_id)
        typer.echo(f"Message #{message_id}: {'approved (queued)' if ok else 'not found or not pending'}")


@app.command("reject")
def cmd_reject(
    message_id: int = typer.Argument(...),
    reason: str = typer.Option("", "--reason"),
):
    """Reject a pending message by id."""
    mgr = ApprovalManager()
    ok = mgr.reject(message_id, reason=reason or None)
    typer.echo(f"Message #{message_id}: {'rejected' if ok else 'not found or not pending'}")


@app.command("send-approved")
def cmd_send_approved(
    practice: str = typer.Option(None),
    dry_run: bool = typer.Option(False, "--dry-run/--live"),
):
    """Send all messages currently in 'approved' status."""
    mgr = ApprovalManager(dry_run=dry_run)
    n = mgr.send_approved(practice_id=practice)
    typer.echo(f"Attempted {n} approved message(s).")


@app.command("simulate-inbound")
def cmd_simulate_inbound(
    from_number: str = typer.Option(..., "--from", help="Sender phone (must match an ingested patient)"),
    body: str = typer.Option(..., help="Message body to simulate"),
    dry_run: bool = typer.Option(True, "--dry-run/--live", help="Skip actually sending replies"),
):
    """Simulate an inbound SMS without involving Twilio. Useful for development."""
    proc = InboundProcessor(dry_run=dry_run)
    result = proc.process_sms(from_number=from_number, body=body)
    typer.echo(f"status:        {result.status}")
    if result.classification:
        typer.echo(f"category:      {result.classification.category}")
        typer.echo(f"confidence:    {result.classification.confidence}")
        typer.echo(f"reasoning:     {result.classification.reasoning}")
        typer.echo(f"suggested:     {result.classification.suggested_action}")
    if result.response_body:
        typer.echo(f"\nresponse:\n{result.response_body}")
    if result.reason:
        typer.echo(f"reason:        {result.reason}")


@app.command("test-call")
def cmd_test_call(
    to: str = typer.Option(..., "--to", help="Number to call, e.g. +14125550123"),
    goal: str = typer.Option(
        "Follow up on their recent inquiry, answer quick questions, and offer to book a time.",
        "--goal", help="What the AI should try to accomplish on the call",
    ),
    business: str = typer.Option("", "--business", help="Business name spoken in the AI disclosure"),
    dry_run: bool = typer.Option(True, "--dry-run/--live", help="Print the plan vs place a real call"),
):
    """Place one AI phone call via Vapi — the voice analog of demo.py.

    --dry-run (default) prints the call plan without dialing (no Vapi account
    needed). --live places a real call (needs VAPI_API_KEY + VAPI_PHONE_NUMBER_ID).
    """
    from .channels.voice import VoiceChannel

    settings = Settings()
    biz = business or settings.business_name or "our office"
    system_prompt = (
        f"You are a warm, professional voice assistant calling on behalf of {biz}. "
        f"Your goal for this call: {goal} "
        "Keep it brief and natural, listen more than you talk, and never pressure. "
        "You already disclosed that you're an AI in your opening line. If the person "
        "asks something you can't answer, wants pricing you don't have, or asks for a "
        "human, warmly offer to have someone call them back, then end the call politely."
    )
    ch = VoiceChannel(settings, dry_run=dry_run)
    res = ch.place_call(to=to, system_prompt=system_prompt, goal=goal, business_name=biz)
    typer.echo(f"call: status={res.status}  id={res.provider_id or '-'}")
    if res.error:
        typer.echo(f"error: {res.error}")


if __name__ == "__main__":
    app()
