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


DEFAULT_WORKFLOW_ROOT = Path("config/workflows")
DEFAULT_PRACTICE_ROOT = Path("config/practices")


def load_workflow(workflow_id: str, root: Path | None = None) -> WorkflowConfig:
    path = (root or DEFAULT_WORKFLOW_ROOT) / f"{workflow_id}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"Workflow config not found: {path}")
    with path.open() as f:
        data = yaml.safe_load(f)
    return WorkflowConfig(**data)


def load_practice(practice_id: str, root: Path | None = None) -> PracticeConfig:
    path = (root or DEFAULT_PRACTICE_ROOT) / f"{practice_id}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"Practice config not found: {path}")
    with path.open() as f:
        data = yaml.safe_load(f)
    return PracticeConfig(**data)


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
