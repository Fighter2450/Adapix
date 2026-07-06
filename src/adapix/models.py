"""Database models."""
from __future__ import annotations

import uuid as _uuid
from datetime import datetime
from enum import Enum
from typing import Any

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# Multi-tenant identity
# ---------------------------------------------------------------------------

class Organization(Base):
    """A business that subscribes to Adapix. Its id is used as practice_id on all data rows."""

    __tablename__ = "organizations"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: str(_uuid.uuid4()))
    name: Mapped[str] = mapped_column(String(200))
    plan: Mapped[str] = mapped_column(String(32), default="trial")  # trial | starter | pro
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    stripe_customer_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    stripe_subscription_id: Mapped[str | None] = mapped_column(String(128), nullable=True)

    # Calling: each org places calls from its OWN dedicated number (its caller ID).
    # Adapix provisions + registers this per business — never a shared number.
    vapi_phone_number_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    phone_number: Mapped[str | None] = mapped_column(String(32), nullable=True)  # +1… for display
    phone_status: Mapped[str] = mapped_column(String(32), default="none")  # none | provisioned | registered

    # iMessage: same principle — each org texts from its OWN dedicated Blooio
    # line (blue bubble on Apple devices), provisioned per business under
    # Adapix's one platform Blooio account. Null = no line yet; texts fall
    # back to Twilio SMS (green).
    blooio_channel_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    imessage_number: Mapped[str | None] = mapped_column(String(32), nullable=True)  # the line's +1… for display

    users: Mapped[list["User"]] = relationship(back_populates="org", cascade="all, delete-orphan")
    profile: Mapped["OrgProfile | None"] = relationship(back_populates="org", uselist=False, cascade="all, delete-orphan")


class EmailConnection(Base):
    """Per-org email connection. Three ways a business sends as itself:
    OAuth (Gmail / Outlook — the login IS the ownership proof) or plain SMTP
    with an app-specific password (covers iCloud, Yahoo, AOL, Zoho, everyone
    else). Either way, follow-up emails go out as them, not a shared sender."""

    __tablename__ = "email_connections"

    org_id: Mapped[str] = mapped_column(String(64), ForeignKey("organizations.id"), primary_key=True)
    provider: Mapped[str] = mapped_column(String(16))  # google | microsoft | smtp
    connected_email: Mapped[str] = mapped_column(String(255))
    connected_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    access_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    refresh_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    expires_at: Mapped[int] = mapped_column(Integer, default=0)  # unix timestamp
    scope: Mapped[str | None] = mapped_column(Text, nullable=True)
    # SMTP-only fields (provider == "smtp")
    smtp_host: Mapped[str | None] = mapped_column(String(255), nullable=True)
    smtp_port: Mapped[int | None] = mapped_column(Integer, nullable=True)
    smtp_password: Mapped[str | None] = mapped_column(Text, nullable=True)  # app-specific password
    connected_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class OrgProfile(Base):
    """Per-tenant practice profile saved by the welcome wizard. Replaces practice_profile.json."""

    __tablename__ = "org_profiles"

    org_id: Mapped[str] = mapped_column(String(64), ForeignKey("organizations.id"), primary_key=True)
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    configured_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    org: Mapped["Organization"] = relationship(back_populates="profile")


class User(Base):
    """A human who logs into Adapix to manage their organization's account."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    org_id: Mapped[str] = mapped_column(String(64), ForeignKey("organizations.id"), index=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    full_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    role: Mapped[str] = mapped_column(String(32), default="owner")  # owner | admin | member
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    org: Mapped["Organization"] = relationship(back_populates="users")


class PatientStatus(str, Enum):
    consulted_not_started = "consulted_not_started"
    treatment_started = "treatment_started"
    explicitly_declined = "explicitly_declined"
    paused = "paused"


class CampaignStatus(str, Enum):
    active = "active"
    completed = "completed"
    escalated = "escalated"
    declined = "declined"
    stopped = "stopped"


class EscalationCategory(str, Enum):
    clinical_question = "clinical_question"
    callback_request = "callback_request"
    decline = "decline"
    emergency = "emergency"
    stop = "stop"
    other = "other"


class Patient(Base):
    """A consult patient. PHI fields: names, phone, email, notes, treatment_*."""

    __tablename__ = "patients"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    practice_id: Mapped[str] = mapped_column(String(64), index=True)
    external_id: Mapped[str | None] = mapped_column(String(128), nullable=True)

    first_name: Mapped[str] = mapped_column(String(100))
    last_name: Mapped[str] = mapped_column(String(100))
    parent_first_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    parent_last_name: Mapped[str | None] = mapped_column(String(100), nullable=True)

    phone: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    preferred_channel: Mapped[str] = mapped_column(String(16), default="sms")

    consult_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    treatment_type: Mapped[str | None] = mapped_column(String(120), nullable=True)
    treatment_plan_amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    status: Mapped[str] = mapped_column(
        String(64), default=PatientStatus.consulted_not_started.value, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    campaigns: Mapped[list["Campaign"]] = relationship(
        back_populates="patient", cascade="all, delete-orphan"
    )


class Campaign(Base):
    """A run of a workflow against one patient."""

    __tablename__ = "campaigns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    practice_id: Mapped[str] = mapped_column(String(64), index=True)
    workflow_id: Mapped[str] = mapped_column(String(64), index=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id"), index=True)

    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    last_step_completed: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(
        String(64), default=CampaignStatus.active.value, index=True
    )

    patient: Mapped["Patient"] = relationship(back_populates="campaigns")
    messages: Mapped[list["Message"]] = relationship(
        back_populates="campaign", cascade="all, delete-orphan"
    )


class Message(Base):
    """A single inbound or outbound message logged for audit + memory."""

    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaigns.id"), index=True)

    direction: Mapped[str] = mapped_column(String(16))
    channel: Mapped[str] = mapped_column(String(16))
    day_in_campaign: Mapped[int | None] = mapped_column(Integer, nullable=True)

    subject: Mapped[str | None] = mapped_column(String(255), nullable=True)
    body: Mapped[str] = mapped_column(Text)

    status: Mapped[str] = mapped_column(String(32), default="composed")
    provider_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    campaign: Mapped["Campaign"] = relationship(back_populates="messages")


class Automation(Base):
    """A scheduled browser automation — visits a URL, extracts data, saves a doc."""

    __tablename__ = "automations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Owning tenant. Nullable for rows created before multi-tenancy; those
    # legacy rows are only visible when no org filter matches (i.e. never
    # through the API).
    org_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    url: Mapped[str] = mapped_column(Text)
    task: Mapped[str] = mapped_column(Text)  # natural-language description of what to extract
    schedule: Mapped[str] = mapped_column(String(64), default="0 9 * * *")  # cron expression
    output_format: Mapped[str] = mapped_column(String(16), default="docx")  # docx | txt | json
    status: Mapped[str] = mapped_column(String(32), default="active", index=True)  # active | paused
    login_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    login_username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    login_password: Mapped[str | None] = mapped_column(String(255), nullable=True)
    login_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_run_status: Mapped[str | None] = mapped_column(String(32), nullable=True)  # ok | error
    last_result_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    runs: Mapped[list["AutomationRun"]] = relationship(
        back_populates="automation", cascade="all, delete-orphan"
    )


class AutomationRun(Base):
    """One execution of an automation — stores the extracted data and output file."""

    __tablename__ = "automation_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    automation_id: Mapped[int] = mapped_column(ForeignKey("automations.id"), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="running")  # running | ok | error
    extracted_data: Mapped[str | None] = mapped_column(Text, nullable=True)  # raw Claude output
    result_path: Mapped[str | None] = mapped_column(Text, nullable=True)  # path to output file
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    automation: Mapped["Automation"] = relationship(back_populates="runs")


class EscalationEvent(Base):
    """A flagged inbound message that needs human attention."""

    __tablename__ = "escalation_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaigns.id"), index=True)
    triggered_by_message_id: Mapped[int | None] = mapped_column(
        ForeignKey("messages.id"), nullable=True
    )
    category: Mapped[str] = mapped_column(String(64), index=True)
    confidence: Mapped[str] = mapped_column(String(16), default="medium")
    reasoning: Mapped[str] = mapped_column(Text, default="")
    suggested_action: Mapped[str] = mapped_column(Text, default="")
    resolved: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
