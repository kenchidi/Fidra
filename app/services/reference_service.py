from datetime import datetime, timedelta, timezone
from typing import Optional
from sqlmodel import Session, select
from fastapi import HTTPException

from app.models.models import (
    ReferenceRequest, StatusEvent, Institution, RequestStatus, utcnow
)
from app.models.schemas import CreateReferenceRequest, ResponsePayload
from app.core.config import get_settings

settings = get_settings()


def _log_status(
    session: Session,
    request: ReferenceRequest,
    to_status: RequestStatus,
    actor: str = "system",
    note: Optional[str] = None,
):
    event = StatusEvent(
        request_id=request.id,
        from_status=request.status.value if request.status else None,
        to_status=to_status.value,
        actor=actor,
        note=note,
    )
    request.status = to_status
    request.updated_at = utcnow()
    session.add(event)
    session.add(request)


def create_reference_request(
    session: Session,
    data: CreateReferenceRequest,
    requesting_institution: Institution,
    base_url: str = "http://localhost:8000",
) -> ReferenceRequest:
    # Resolve responding institution by bank code
    responding = session.exec(
        select(Institution).where(Institution.bank_code == data.referee_bank_code)
    ).first()

    expires = utcnow() + timedelta(days=settings.token_expire_days)

    req = ReferenceRequest(
        requesting_institution_id=requesting_institution.id,
        responding_institution_id=responding.id if responding else None,
        customer_full_name=data.customer_full_name,
        customer_phone=data.customer_phone,
        customer_email=data.customer_email,
        customer_bvn=data.customer_bvn,
        referee_account_number=data.referee_account_number,
        referee_bank_code=data.referee_bank_code,
        referee_account_name=data.referee_account_name,
        purpose=data.purpose,
        consent_given=data.consent_given,
        consent_at=utcnow() if data.consent_given else None,
        expires_at=expires,
        status=RequestStatus.submitted if data.consent_given else RequestStatus.pending_consent,
    )
    session.add(req)
    session.commit()
    session.refresh(req)

    # Log initial status
    _log_status(session, req, req.status, actor="institution", note="Request created")
    session.commit()

    # Move to referee_notified (in real system this would trigger SMS)
    _log_status(session, req, RequestStatus.referee_notified, actor="system", note="Referee notified (simulated)")
    session.commit()
    session.refresh(req)

    return req


def get_request_by_public_id(session: Session, public_id: str) -> ReferenceRequest:
    req = session.exec(
        select(ReferenceRequest).where(ReferenceRequest.public_id == public_id)
    ).first()
    if not req:
        raise HTTPException(status_code=404, detail="Reference request not found")
    return req


def get_request_by_referee_token(session: Session, token: str) -> ReferenceRequest:
    req = session.exec(
        select(ReferenceRequest).where(ReferenceRequest.referee_token == token)
    ).first()
    if not req:
        raise HTTPException(status_code=404, detail="Invalid or expired link")
    if req.expires_at and req.expires_at < utcnow():
        if req.status not in (RequestStatus.completed, RequestStatus.declined, RequestStatus.expired):
            _log_status(session, req, RequestStatus.expired, note="Token expired")
            session.commit()
        raise HTTPException(status_code=410, detail="This reference link has expired")
    return req


def get_request_by_customer_token(session: Session, token: str) -> ReferenceRequest:
    req = session.exec(
        select(ReferenceRequest).where(ReferenceRequest.customer_token == token)
    ).first()
    if not req:
        raise HTTPException(status_code=404, detail="Invalid or expired link")
    return req


def mark_referee_viewed(session: Session, req: ReferenceRequest):
    if req.status == RequestStatus.referee_notified:
        _log_status(session, req, RequestStatus.referee_viewed, actor="referee")
        session.commit()


def authorize_request(
    session: Session,
    req: ReferenceRequest,
    payload: ResponsePayload,
):
    if req.status in (RequestStatus.completed, RequestStatus.declined, RequestStatus.expired, RequestStatus.cancelled):
        raise HTTPException(status_code=400, detail=f"Request already in terminal state: {req.status}")

    req.response_payload = payload.model_dump()
    req.completed_at = utcnow()
    _log_status(session, req, RequestStatus.authorized, actor="referee", note="Referee authorized")
    _log_status(session, req, RequestStatus.completed, actor="system", note="Reference completed")
    session.commit()
    session.refresh(req)
    return req


def decline_request(session: Session, req: ReferenceRequest, reason: Optional[str] = None):
    if req.status in (RequestStatus.completed, RequestStatus.declined, RequestStatus.expired, RequestStatus.cancelled):
        raise HTTPException(status_code=400, detail=f"Request already in terminal state: {req.status}")

    _log_status(session, req, RequestStatus.declined, actor="referee", note=reason or "Declined by referee")
    session.commit()
    session.refresh(req)
    return req


def get_history(session: Session, request_id: int):
    return session.exec(
        select(StatusEvent)
        .where(StatusEvent.request_id == request_id)
        .order_by(StatusEvent.created_at)
    ).all()
