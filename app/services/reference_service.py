from datetime import timedelta
from typing import Optional
from sqlmodel import Session, select
from fastapi import HTTPException

from app.models.models import (
    ReferenceRequest, StatusEvent, Institution, RequestStatus, AssuranceLevel, utcnow
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
        # Path B: the referee's bank answers from its inbox (or through the API)
        _log_status(
            session, req, RequestStatus.pending_bank_confirmation,
            actor="system",
            note=f"Waiting for {responding.name} to confirm the account",
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
        # Path B: the referee only agrees to share. The bank supplies the confirmation.
        if req.referee_consent_at:
            return req
        req.referee_consent_at = utcnow()
        req.referee_answer = {
            "relationship_length_band": payload.relationship_length_band,
            "additional_comments": payload.additional_comments,
        }
        _log_status(session, req, req.status, actor="referee", note="Referee agreed to share")
        _maybe_complete_path_b(session, req, responding)
        session.commit()
        session.refresh(req)
        return req
    else:
        # Path A: self-attested by referee
        data = payload.model_dump()
        data["assurance_level"] = AssuranceLevel.self_attested.value
        data["confirmation_type"] = "self_attested"
        data["account_verified"] = False
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


def _maybe_complete_path_b(session: Session, req: ReferenceRequest, responding: Optional[Institution]):
    """A Path B request completes once the bank has answered AND the referee has agreed."""
    if not (req.bank_answer and req.referee_consent_at):
        return
    bank = req.bank_answer
    ref = req.referee_answer or {}
    name = responding.name if responding else "the bank"
    req.response_payload = {
        "account_exists": True,
        "account_active": bank.get("account_active"),
        "account_name_match": bank.get("name_match"),
        "good_standing": bank.get("good_standing"),
        "relationship_length_band": bank.get("relationship_length_band") or ref.get("relationship_length_band"),
        "additional_comments": ref.get("additional_comments"),
        "bank_comments": bank.get("comments"),
        "referee_agreed": True,
        "responded_by": responding.code if responding else None,
        "responded_by_name": name,
        "assurance_level": AssuranceLevel.bank_confirmed.value,
        "confirmation_type": "bank_confirmed",
    }
    req.assurance_level = AssuranceLevel.bank_confirmed.value
    req.completed_at = utcnow()
    _log_status(session, req, RequestStatus.authorized, actor="bank", note=f"Confirmed by {name}")
    _log_status(session, req, RequestStatus.completed, actor="system", note="Reference completed")


def submit_bank_response(session: Session, req: ReferenceRequest, institution: Institution, body):
    """The referee's bank answers a Path B request. Used by the dashboard inbox and the API."""
    if req.path != "path_b" or req.responding_institution_id != institution.id:
        raise HTTPException(status_code=404, detail="Reference request not found")
    if req.status != RequestStatus.pending_bank_confirmation:
        raise HTTPException(status_code=400, detail=f"This request is no longer waiting for the bank (status: {req.status.value})")
    if req.expires_at and req.expires_at < utcnow():
        raise HTTPException(status_code=400, detail="This request has expired")

    if not body.account_exists:
        req.response_payload = {
            "account_exists": False,
            "account_active": False,
            "responded_by": institution.code,
            "responded_by_name": institution.name,
            "assurance_level": AssuranceLevel.bank_confirmed.value,
            "confirmation_type": "bank_confirmed",
        }
        req.completed_at = utcnow()
        _log_status(session, req, RequestStatus.failed, actor="bank", note=f"Account not found at {institution.name}")
    else:
        req.bank_answer = {
            "account_active": body.account_active,
            "name_match": body.name_match,
            "good_standing": body.good_standing,
            "relationship_length_band": body.relationship_length_band,
            "comments": body.comments,
        }
        _log_status(session, req, req.status, actor="bank", note=f"{institution.name} confirmed the account")
        _maybe_complete_path_b(session, req, institution)
    session.commit()
    session.refresh(req)
    return req


def pending_for_bank(session: Session, institution_id: int):
    """Path B requests waiting for this bank, newest first, excluding expired ones."""
    rows = session.exec(
        select(ReferenceRequest)
        .where(
            ReferenceRequest.responding_institution_id == institution_id,
            ReferenceRequest.path == "path_b",
            ReferenceRequest.status == RequestStatus.pending_bank_confirmation,
        )
        .order_by(ReferenceRequest.created_at.desc())
    ).all()
    now = utcnow()
    return [r for r in rows if not (r.expires_at and r.expires_at < now)]
