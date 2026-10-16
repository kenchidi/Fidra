from datetime import timedelta
from typing import Optional
from sqlmodel import Session, select
from fastapi import HTTPException

from app.models.models import (
    ReferenceRequest, StatusEvent, Institution, RequestStatus, AssuranceLevel, utcnow
)
from app.models.schemas import CreateReferenceRequest, ResponsePayload
from app.core.config import get_settings
from app.services import mock_bank

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
        select(Institution).where(
            Institution.bank_code == data.referee_bank_code,
            Institution.is_active == True,
        )
    ).first()

    # Path B if referee's bank is on the network and connected
    use_path_b = bool(
        responding and responding.network_connected and responding.is_active
    )

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
        path="path_b" if use_path_b else "path_a",
        assurance_level=(
            AssuranceLevel.bank_confirmed.value
            if use_path_b
            else AssuranceLevel.self_attested.value
        ),
        status=RequestStatus.submitted if data.consent_given else RequestStatus.pending_consent,
    )
    session.add(req)
    session.commit()
    session.refresh(req)

    _log_status(session, req, req.status, actor="institution", note="Request created")
    session.commit()

    if use_path_b and responding:
        # Path B: mock bank verifies account, then await consent/confirmation
        check = mock_bank.verify_account(
            responding, data.referee_account_number, data.referee_account_name
        )
        if not check.get("account_exists"):
            req.response_payload = {
                **check,
                "assurance_level": "bank_confirmed",
                "responded_by": responding.code,
            }
            req.completed_at = utcnow()
            _log_status(
                session, req, RequestStatus.failed,
                actor="bank",
                note=f"Account not found at {responding.name}",
            )
            session.commit()
            session.refresh(req)
            return req

        _log_status(
            session, req, RequestStatus.pending_bank_confirmation,
            actor="system",
            note=f"Account verified at {responding.name}; awaiting referee consent",
        )
        session.commit()
        session.refresh(req)
        return req

    # Path A: notify referee via link
    _log_status(
        session, req, RequestStatus.referee_notified,
        actor="system",
        note="Referee notified (simulated)",
    )
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
        if req.status not in (
            RequestStatus.completed, RequestStatus.declined,
            RequestStatus.expired, RequestStatus.failed,
        ):
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
    if req.status in (RequestStatus.referee_notified, RequestStatus.pending_bank_confirmation):
        if req.status == RequestStatus.referee_notified:
            _log_status(session, req, RequestStatus.referee_viewed, actor="referee")
            session.commit()


def authorize_request(
    session: Session,
    req: ReferenceRequest,
    payload: ResponsePayload,
):
    terminal = (
        RequestStatus.completed, RequestStatus.declined,
        RequestStatus.expired, RequestStatus.cancelled, RequestStatus.failed,
    )
    if req.status in terminal:
        raise HTTPException(status_code=400, detail=f"Request already in terminal state: {req.status}")

    responding = None
    if req.responding_institution_id:
        responding = session.get(Institution, req.responding_institution_id)

    if req.path == "path_b" and responding:
        # Path B: response is bank-confirmed via mock connector
        check = mock_bank.verify_account(
            responding, req.referee_account_number, req.referee_account_name
        )
        req.response_payload = mock_bank.build_bank_reference_response(
            responding,
            check,
            good_standing=payload.good_standing,
            relationship_length_band=payload.relationship_length_band or "3-5_years",
            comments=payload.additional_comments,
        )
        req.assurance_level = AssuranceLevel.bank_confirmed.value
        actor = "bank"
        note = f"Confirmed by {responding.name}"
    else:
        # Path A: self-attested by referee
        data = payload.model_dump()
        data["assurance_level"] = AssuranceLevel.self_attested.value
        data["confirmation_type"] = "self_attested"
        data["responded_by"] = "referee_self"
        req.response_payload = data
        req.assurance_level = AssuranceLevel.self_attested.value
        actor = "referee"
        note = "Referee authorized"

    req.completed_at = utcnow()
    _log_status(session, req, RequestStatus.authorized, actor=actor, note=note)
    _log_status(session, req, RequestStatus.completed, actor="system", note="Reference completed")
    session.commit()
    session.refresh(req)
    return req


def decline_request(session: Session, req: ReferenceRequest, reason: Optional[str] = None):
    terminal = (
        RequestStatus.completed, RequestStatus.declined,
        RequestStatus.expired, RequestStatus.cancelled, RequestStatus.failed,
    )
    if req.status in terminal:
        raise HTTPException(status_code=400, detail=f"Request already in terminal state: {req.status}")

    _log_status(
        session, req, RequestStatus.declined,
        actor="referee",
        note=reason or "Declined by referee",
    )
    session.commit()
    session.refresh(req)
    return req


def get_history(session: Session, request_id: int):
    return session.exec(
        select(StatusEvent)
        .where(StatusEvent.request_id == request_id)
        .order_by(StatusEvent.created_at.asc())
    ).all()
