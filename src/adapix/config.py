"""Configuration loading.

Two kinds of configs:
  1. Workflows  - what the agent does (case_acceptance, recall, etc.)
  2. Practices  - who the agent works for (Steel City Orthodontics, etc.)

Plus environment settings (API keys, DB URL, security) loaded from .env.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Anthropic
    anthropic_api_key: str
    adapix_model: str = "claude-sonnet-4-6"

    # Twilio (SMS)
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_from_number: str = ""

    # Resend (email)
    resend_api_key: str = ""
    resend_from_email: str = ""

    # Blooio (iMessage — blue-bubble texts on Apple devices, with Blooio's
    # own RCS/SMS fallback for Android). When configured, texts try Blooio
    # FIRST and fall back to Twilio SMS on failure — same preference pattern
    # as the org's connected Gmail over the shared Resend sender.
    blooio_api_key: str = ""
    blooio_channel_id: str = ""

    # Vapi (AI voice calling). Vapi handles telephony + speech + turn-taking;
    # we bring the model (Claude) and the prompt. Get these from the Vapi
    # dashboard after buying a phone number.
    vapi_api_key: str = ""
    vapi_phone_number_id: str = ""          # the Vapi number that places calls
    vapi_voice_provider: str = "11labs"     # ElevenLabs voice by default
    vapi_voice_id: str = "burt"
    # Vapi model provider/name may differ from the SMS/email model id — verify
    # against Vapi's supported list. Defaults to Anthropic + your adapix_model.
    vapi_model_provider: str = "anthropic"

    # Business name spoken in the AI-disclosure opening line on calls.
    # Falls back to this if the practice profile doesn't supply one.
    business_name: str = "our office"
    # Auto-provision a dedicated calling number for each org at signup.
    # Set false while testing so test signups don't each create a number.
    auto_provision_numbers: bool = True

    # Database
    database_url: str = "sqlite:///./adapix.db"

    # Admin UI auth (HTTP Basic). Leave blank in dev to disable.
    admin_username: str = ""
    admin_password: str = ""

    # Webhook security
    # If true, skip Twilio signature verification (dev only — never in prod).
    skip_twilio_verification: bool = False
    # Public base URL of this server. Required for Twilio sig verification
    # when behind a proxy / ngrok. Example: https://adapix.ngrok.io
    public_base_url: str = ""

    # OAuth — Google Workspace (Gmail send-as practice email)
    google_client_id: str = ""
    google_client_secret: str = ""

    # OAuth — Microsoft 365 (Outlook send-as practice email)
    microsoft_client_id: str = ""
    microsoft_client_secret: str = ""
    microsoft_tenant_id: str = "common"   # "common" = multi-tenant


class CadenceStep(BaseModel):
    day: int
    channel: str
    intent: str


class EscalationRule(BaseModel):
    trigger: str
    action: str


class WorkflowConfig(BaseModel):
    id: str
    name: str
    description: str
    version: int = 1
    objective: str
    voice: dict[str, Any] = Field(default_factory=dict)
    cadence: list[CadenceStep] = Field(default_factory=list)
    escalation: list[EscalationRule] = Field(default_factory=list)
    knowledge_required: list[str] = Field(default_factory=list)
    success_metric: dict[str, Any] = Field(default_factory=dict)
    target: dict[str, Any] = Field(default_factory=dict)


class PracticeConfig(BaseModel):
    id: str
    name: str
    doctor_name: str
    office_phone: str
    office_hours: str
    typical_treatment_duration: str = ""
    financing_options: list[str] = Field(default_factory=list)
    additional_knowledge: dict[str, Any] = Field(default_factory=dict)
    approval_mode: str = "required"  # "required" | "auto"


# Anchored to the repo root (…/src/adapix/config.py → three parents up), NOT
# the process CWD — a server started from any other directory would otherwise
# find zero workflows and the engine would silently do nothing.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_WORKFLOW_ROOT = _REPO_ROOT / "config" / "workflows"
DEFAULT_PRACTICE_ROOT = _REPO_ROOT / "config" / "practices"


def load_workflow(workflow_id: str, root: Path | None = None) -> WorkflowConfig:
    path = (root or DEFAULT_WORKFLOW_ROOT) / f"{workflow_id}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"Workflow config not found: {path}")
    with path.open() as f:
        data = yaml.safe_load(f)
    return WorkflowConfig(**data)


def load_practice(practice_id: str, root: Path | None = None) -> PracticeConfig:
    path = (root or DEFAULT_PRACTICE_ROOT) / f"{practice_id}.yaml"
    if path.exists():
        with path.open() as f:
            data = yaml.safe_load(f)
        return PracticeConfig(**data)

    # No YAML — this is a DB-backed org (the multi-tenant path). Build the
    # config from the org's profile instead. This is what makes the campaign
    # engine actually RUN for real signups: before this fallback, every DB
    # org silently failed here with FileNotFoundError and never composed a
    # single campaign step. It also routes everything the owner taught
    # Adapix (Database tab: description, services & pricing, Q&A knowledge,
    # tone) into the composer's prompt via additional_knowledge, and honors
    # the Follow-up rules page's auto-approve setting via approval_mode.
    from .practice import load_profile

    profile = load_profile(practice_id)
    rules = getattr(profile, "rules", {}) or {}

    additional: dict[str, Any] = {}
    if profile.description.strip():
        additional["what_this_business_does"] = profile.description.strip()
    services_str = "; ".join(
        f"{e['name'].strip()}: {profile.format_service_price(e)}" if (e.get("price") or "").strip() else e["name"].strip()
        for e in (profile.services or []) if (e.get("name") or "").strip()
    )
    if services_str:
        additional["services_and_pricing"] = services_str
    qa = " | ".join(
        f"Q: {e['q'].strip()} A: {e['a'].strip()}"
        for e in (profile.knowledge_base or [])
        if (e.get("q") or "").strip() and (e.get("a") or "").strip()
    )
    if qa:
        additional["owner_taught_answers"] = qa
    additional["tone_guidance"] = profile.tone_guidance()

    return PracticeConfig(
        id=practice_id,
        name=profile.practice_name,
        doctor_name=profile.doctor,
        office_phone=profile.phone or "(not configured)",
        office_hours=profile.hours or "(not configured)",
        additional_knowledge=additional,
        approval_mode="auto" if rules.get("auto_approve") else "required",
    )


def list_workflows(root: Path | None = None) -> list[str]:
    base = root or DEFAULT_WORKFLOW_ROOT
    if not base.exists():
        return []
    return sorted(p.stem for p in base.glob("*.yaml"))


def list_practices(root: Path | None = None) -> list[str]:
    base = root or DEFAULT_PRACTICE_ROOT
    if not base.exists():
        return []
    return sorted(p.stem for p in base.glob("*.yaml"))
