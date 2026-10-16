from fastapi import APIRouter, Depends, Request
from sqlmodel import Session, select
from typing import List

from app.core.database import get_session
from app.api.deps import get_current_institution
from app.models.models import Institution, ReferenceRequest, RequestStatus
from app.models.schemas import (
    CreateReferenceRequest,
    ReferenceRequestOut,
    ReferenceRequestDetail,
    StatusEventOut,
    InstitutionCreate,
    InstitutionOut,
    InstitutionPublic,
    AuthorizeBody,
    DeclineBody,
    ResponsePayload,
)
from app.services import reference_service as svc

router = APIRouter()


def _build_urls(req: ReferenceRequest, base: str) -> dict:
    return {
        "customer_status_url": f"{base}/c/{req.customer_token}",
        "referee_authorize_url": f"{base}/r/{req.referee_token}",
    }


# ---------- Institutions (bootstrap) ----------

@router.post("/institutions", response_model=InstitutionOut, tags=["Institutions"])
def create_institution(
    body: InstitutionCreate,
    session: Session = Depends(get_session),
):
    existing = session.exec(select(Institution).where(Institution.code == body.code)).first()
    if existing:
        return existing

    inst = Institution(code=body.code, name=body.name, bank_code=body.bank_code, network_connected=getattr(body, 'network_connected', False))
    session.add(inst)
    session.commit()
    session.refresh(inst)
    return inst


@router.get("/institutions", response_model=List[InstitutionPublic], tags=["Institutions"])
def list_institutions(
    institution: Institution = Depends(get_current_institution),
    session: Session = Depends(get_session),
):
    """List institutions on the network. Requires authentication. API keys are never returned."""
    rows = session.exec(select(Institution).where(Institution.is_active == True)).all()
    return [InstitutionPublic.model_validate(r) for r in rows]


# ---------- Reference Requests ----------

@router.post("/reference-requests", response_model=ReferenceRequestOut, tags=["References"])
def create_reference(
    body: CreateReferenceRequest,
    request: Request,
    institution: Institution = Depends(get_current_institution),
    session: Session = Depends(get_session),
):
    base = str(request.base_url).rstrip("/")
    req = svc.create_reference_request(session, body, institution, base_url=base)
    urls = _build_urls(req, base)

    return ReferenceRequestOut(
        public_id=req.public_id,
        status=req.status,
        path=getattr(req, "path", "path_a"),
        assurance_level=getattr(req, "assurance_level", "self_attested"),
        customer_full_name=req.customer_full_name,
        referee_account_number=req.referee_account_number,
        referee_bank_code=req.referee_bank_code,
        purpose=req.purpose,
        created_at=req.created_at,
        expires_at=req.expires_at,
        completed_at=req.completed_at,
        response_payload=req.response_payload,
        **urls,
    )


@router.get("/reference-requests/{public_id}", response_model=ReferenceRequestDetail, tags=["References"])
def get_reference(
    public_id: str,
    request: Request,
    institution: Institution = Depends(get_current_institution),
    session: Session = Depends(get_session),
):
    req = svc.get_request_by_public_id(session, public_id)
    # Optional: enforce that only the requesting institution can see it
    history = svc.get_history(session, req.id)
    base = str(request.base_url).rstrip("/")
    urls = _build_urls(req, base)

    return ReferenceRequestDetail(
        public_id=req.public_id,
        status=req.status,
        path=getattr(req, "path", "path_a"),
        assurance_level=getattr(req, "assurance_level", "self_attested"),
        customer_full_name=req.customer_full_name,
        referee_account_number=req.referee_account_number,
        referee_bank_code=req.referee_bank_code,
        purpose=req.purpose,
        created_at=req.created_at,
        expires_at=req.expires_at,
        completed_at=req.completed_at,
        response_payload=req.response_payload,
        history=[StatusEventOut.model_validate(h) for h in history],
        **urls,
    )


@router.get("/reference-requests", response_model=List[ReferenceRequestOut], tags=["References"])
def list_references(
    request: Request,
    institution: Institution = Depends(get_current_institution),
    session: Session = Depends(get_session),
):
    reqs = session.exec(
        select(ReferenceRequest)
        .where(ReferenceRequest.requesting_institution_id == institution.id)
        .order_by(ReferenceRequest.created_at.desc())
    ).all()
    base = str(request.base_url).rstrip("/")
    results = []
    for req in reqs:
        urls = _build_urls(req, base)
        results.append(ReferenceRequestOut(
            public_id=req.public_id,
            status=req.status,
            path=getattr(req, "path", "path_a"),
            assurance_level=getattr(req, "assurance_level", "self_attested"),
            customer_full_name=req.customer_full_name,
            referee_account_number=req.referee_account_number,
            referee_bank_code=req.referee_bank_code,
            purpose=req.purpose,
            created_at=req.created_at,
            expires_at=req.expires_at,
            completed_at=req.completed_at,
            response_payload=req.response_payload,
            **urls,
        ))
    return results
