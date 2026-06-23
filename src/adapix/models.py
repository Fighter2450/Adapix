"""Database models. Patients, campaigns, messages, escalation events.

Schema is intentionally minimal for v0. PHI fields are flagged in comments;
when we move to production we encrypt those columns at rest.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


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
