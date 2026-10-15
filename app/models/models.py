from datetime import datetime, timezone
from enum import Enum
from typing import Optional
from sqlmodel import SQLModel, Field, Column, JSON
from sqlalchemy import Text
import secrets


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def generate_token(prefix: str = "") -> str:
    return f"{prefix}{secrets.token_urlsafe(24)}"


class RequestStatus(str, Enum):
    draft = "draft"
    pending_consent = "pending_consent"
    submitted = "submitted"
    referee_notified = "referee_notified"
    referee_viewed = "referee_viewed"
    authorized = "authorized"
    declined = "declined"
    completed = "completed"
    expired = "expired"
    cancelled = "cancelled"
    failed = "failed"


class Institution(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    code: str = Field(index=True, unique=True)          # e.g. "gtb", "firstbank"
    name: str
    bank_code: Optional[str] = Field(default=None, index=True)  # NUBAN bank code
    api_key: str = Field(default_factory=lambda: generate_token("rk_"))
    is_active: bool = True
    created_at: datetime = Field(default_factory=utcnow)


class ReferenceRequest(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    public_id: str = Field(default_factory=lambda: generate_token("req_"), index=True, unique=True)

    # Institutions
    requesting_institution_id: int = Field(foreign_key="institution.id")
    responding_institution_id: Optional[int] = Field(default=None, foreign_key="institution.id")

    # Customer (the person being verified)
    customer_full_name: str
    customer_phone: Optional[str] = None
    customer_email: Optional[str] = None
    customer_bvn: Optional[str] = None

    # Referee
    referee_account_number: str
    referee_bank_code: str
    referee_account_name: Optional[str] = None

    purpose: str = "account_opening"  # account_opening | loan | vendor | other
    status: RequestStatus = Field(default=RequestStatus.draft, index=True)

    # Tokens for public pages
    customer_token: str = Field(default_factory=lambda: generate_token("cust_"), index=True)
    referee_token: str = Field(default_factory=lambda: generate_token("ref_"), index=True)

    # Response data (populated on completion)
    response_payload: Optional[dict] = Field(default=None, sa_column=Column(JSON))

    # Timestamps
    created_at: datetime = Field(default_factory=utcnow)
    expires_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    updated_at: datetime = Field(default_factory=utcnow)

    # Consent
    consent_given: bool = False
    consent_at: Optional[datetime] = None


class StatusEvent(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    request_id: int = Field(foreign_key="referencerequest.id", index=True)
    from_status: Optional[str] = None
    to_status: str
    actor: str = "system"          # system | customer | referee | institution
    note: Optional[str] = None
    created_at: datetime = Field(default_factory=utcnow)
