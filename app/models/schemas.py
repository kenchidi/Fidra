from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, Field
from app.models.models import RequestStatus


class CreateReferenceRequest(BaseModel):
    customer_full_name: str
    customer_phone: Optional[str] = None
    customer_email: Optional[str] = None
    customer_bvn: Optional[str] = None

    referee_account_number: str
    referee_bank_code: str
    referee_account_name: Optional[str] = None

    purpose: str = "account_opening"
    consent_given: bool = True


class ResponsePayload(BaseModel):
    account_exists: Optional[bool] = None  # None = not verified
    account_name_match: Optional[bool] = None
    relationship_length_band: Optional[str] = None
    good_standing: bool = True
    last_activity_band: Optional[str] = None
    additional_comments: Optional[str] = None
    responded_by: str = "referee_self"


class ReferenceRequestOut(BaseModel):
    public_id: str
    status: RequestStatus
    path: str = "path_a"
    assurance_level: str = "self_attested"
    customer_full_name: str
    referee_account_number: str
    referee_bank_code: str
    purpose: str
    created_at: datetime
    expires_at: Optional[datetime]
    completed_at: Optional[datetime]
    response_payload: Optional[dict] = None
    customer_status_url: Optional[str] = None
    referee_authorize_url: Optional[str] = None

    class Config:
        from_attributes = True


class StatusEventOut(BaseModel):
    from_status: Optional[str]
    to_status: str
    actor: str
    note: Optional[str]
    created_at: datetime

    class Config:
        from_attributes = True


class ReferenceRequestDetail(ReferenceRequestOut):
    history: List[StatusEventOut] = []


class AuthorizeBody(BaseModel):
    account_exists: bool = True
    good_standing: bool = True
    relationship_length_band: Optional[str] = "1-3_years"
    additional_comments: Optional[str] = None


class DeclineBody(BaseModel):
    reason: Optional[str] = None


class InstitutionCreate(BaseModel):
    code: str
    name: str
    bank_code: Optional[str] = None
    network_connected: bool = False


class InstitutionOut(BaseModel):
    id: int
    code: str
    name: str
    bank_code: Optional[str]
    api_key: str
    is_active: bool
    network_connected: bool = False

    class Config:
        from_attributes = True


class InstitutionPublic(BaseModel):
    id: int
    code: str
    name: str
    bank_code: Optional[str]
    is_active: bool
    network_connected: bool = False

    class Config:
        from_attributes = True
