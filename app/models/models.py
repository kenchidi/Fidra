from datetime import datetime, timezone
from enum import Enum
from typing import Optional
from sqlmodel import SQLModel, Field, Column, JSON
import hashlib
import secrets


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def generate_token(prefix: str = "") -> str:
    return f"{prefix}{secrets.token_urlsafe(24)}"


def hash_api_key(key: str) -> str:
    """API keys are long random strings, so a plain SHA-256 is sufficient (no salt needed)."""
    return hashlib.sha256(key.encode()).hexdigest()


def new_api_key() -> tuple:
    plain = generate_token("rk_")
    return plain, hash_api_key(plain)


class RequestStatus(str, Enum):
    draft = "draft"
    pending_consent = "pending_consent"
    submitted = "submitted"
    referee_notified = "referee_notified"
    referee_viewed = "referee_viewed"
    pending_bank_confirmation = "pending_bank_confirmation"
    authorized = "authorized"
    declined = "declined"
    completed = "completed"
    expired = "expired"
    cancelled = "cancelled"
    failed = "failed"


class AssuranceLevel(str, Enum):
    self_attested = "self_attested"
    otp_verified = "otp_verified"
    bank_confirmed = "bank_confirmed"


class Institution(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    code: str = Field(index=True, unique=True)
    name: str
    bank_code: Optional[str] = Field(default=None, index=True)
    # Only a hash is stored. The plain key is shown once, when the institution is created.
    api_key_hash: str = Field(index=True, unique=True)
    api_key_prefix: str = ""
    is_active: bool = True
    # Path B: institution can act as responding bank on the network
    network_connected: bool = False
    created_at: datetime = Field(default_factory=utcnow)


class ReferenceRequest(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    public_id: str = Field(default_factory=lambda: generate_token("req_"), index=True, unique=True)

    requesting_institution_id: int = Field(foreign_key="institution.id")
    responding_institution_id: Optional[int] = Field(default=None, foreign_key="institution.id")

    customer_full_name: str
    customer_phone: Optional[str] = None
    customer_email: Optional[str] = None
    customer_bvn: Optional[str] = None

    referee_account_number: str
    referee_bank_code: str
    referee_account_name: Optional[str] = None

    purpose: str = "account_opening"
    status: RequestStatus = Field(default=RequestStatus.draft, index=True)

    # path_a = referee link; path_b = bank-confirmed
    path: str = Field(default="path_a", index=True)
    assurance_level: str = Field(default=AssuranceLevel.self_attested.value)

    customer_token: str = Field(default_factory=lambda: generate_token("cust_"), index=True)
    referee_token: str = Field(default_factory=lambda: generate_token("ref_"), index=True)

    response_payload: Optional[dict] = Field(default=None, sa_column=Column(JSON))

    created_at: datetime = Field(default_factory=utcnow)
    expires_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    updated_at: datetime = Field(default_factory=utcnow)

    consent_given: bool = False
    consent_at: Optional[datetime] = None

    # Path B: the request completes only when both the referee and the bank have answered
    referee_consent_at: Optional[datetime] = None
    referee_answer: Optional[dict] = Field(default=None, sa_column=Column(JSON))
    bank_answer: Optional[dict] = Field(default=None, sa_column=Column(JSON))


class StaffUser(SQLModel, table=True):
    """A person at an institution who signs in to the Fidra dashboard."""
    id: Optional[int] = Field(default=None, primary_key=True)
    institution_id: int = Field(foreign_key="institution.id", index=True)
    email: str = Field(index=True, unique=True)
    full_name: str
    role: str = "officer"  # officer | supervisor | admin
    password_hash: str
    is_active: bool = True
    must_change_password: bool = False  # true for logins created by an admin with a temporary password
    created_at: datetime = Field(default_factory=utcnow)


class StatusEvent(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    request_id: int = Field(foreign_key="referencerequest.id", index=True)
    from_status: Optional[str] = None
    to_status: str
    actor: str = "system"
    note: Optional[str] = None
    created_at: datetime = Field(default_factory=utcnow)
