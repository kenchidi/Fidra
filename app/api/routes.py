from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import Response
from sqlmodel import Session, select
from typing import List

from app.core.database import get_session
from app.api.deps import get_current_institution, require_admin
from app.models.models import Institution, ReferenceRequest, RequestStatus
from app.models.schemas import (
    CreateReferenceRequest,
    StaffCreate,
    BankResponseBody,
    BankResponseResult,
    InboxItem,
    SignedFormResult,
    StaffOut,
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
from app.services.institution_service import create_institution as create_institution_record
from app.core.security import hash_password
from app.models.models import StaffUser

router = APIRouter()


def _build_urls(req: ReferenceRequest, base: str) -> dict:
    return {
        "customer_status_url": f"{base}/c/{req.customer_token}",
        "referee_authorize_url": f"{base}/r/{req.referee_token}",
    }


# ---------- Institutions (bootstrap) ----------

@router.post(
    "/institutions",
    response_model=InstitutionOut,
    tags=["Institutions"],
    dependencies=[Depends(require_admin)],
)
def create_institution(
    body: InstitutionCreate,
    session: Session = Depends(get_session),
):
    """Register an institution. Requires the admin key (X-Admin-Key).
    The API key is returned once, in this response, and cannot be retrieved later."""
    if session.exec(select(Institution).where(Institution.code == body.code)).first():
        raise HTTPException(status_code=409, detail="An institution with this code already exists")
    if body.bank_code and session.exec(
        select(Institution).where(Institution.bank_code == body.bank_code, Institution.is_active == True)
    ).first():
        raise HTTPException(status_code=409, detail="An active institution already uses this bank code")

    inst, plain_key = create_institution_record(
        session,
        code=body.code,
        name=body.name,
        bank_code=body.bank_code,
        network_connected=body.network_connected,
    )
    return InstitutionOut(
        id=inst.id,
        code=inst.code,
        name=inst.name,
        bank_code=inst.bank_code,
        api_key=plain_key,
        is_active=inst.is_active,
        network_connected=inst.network_connected,
    )


@router.post(
    "/institutions/{code}/staff",
    response_model=StaffOut,
    tags=["Institutions"],
    dependencies=[Depends(require_admin)],
)
def create_staff(code: str, body: StaffCreate, session: Session = Depends(get_session)):
    """Create a dashboard login for an institution's staff. Requires the admin key.
    Roles: officer, supervisor, admin. The password must be at least 10 characters."""
    inst = session.exec(select(Institution).where(Institution.code == code)).first()
    if not inst:
        raise HTTPException(status_code=404, detail="Institution not found")
    if body.role not in ("officer", "supervisor", "admin"):
        raise HTTPException(status_code=422, detail="Role must be officer, supervisor or admin")
    if len(body.password) < 10:
        raise HTTPException(status_code=422, detail="Password must be at least 10 characters")
    email = body.email.strip().lower()
    if session.exec(select(StaffUser).where(StaffUser.email == email)).first():
        raise HTTPException(status_code=409, detail="A staff account with this email already exists")
    user = StaffUser(institution_id=inst.id, email=email, full_name=body.full_name.strip(),
                     role=body.role, password_hash=hash_password(body.password), must_change_password=True)
    session.add(user); session.commit(); session.refresh(user)
    return StaffOut(id=user.id, email=user.email, full_name=user.full_name, role=user.role, institution_code=inst.code)


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
    # Only the requesting or the responding institution may see a request.
    # Return 404 (not 403) so request IDs cannot be probed.
    if institution.id not in (req.requesting_institution_id, req.responding_institution_id):
        raise HTTPException(status_code=404, detail="Reference request not found")
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


@router.get("/inbox", response_model=List[InboxItem], tags=["Bank responses"])
def inbox(
    institution: Institution = Depends(get_current_institution),
    session: Session = Depends(get_session),
):
    """Path B requests waiting for your bank to confirm an account."""
    out = []
    for r in svc.pending_for_bank(session, institution.id):
        requester = session.get(Institution, r.requesting_institution_id)
        out.append(InboxItem(
            public_id=r.public_id, customer_full_name=r.customer_full_name,
            referee_account_number=r.referee_account_number, purpose=r.purpose,
            requesting_institution=requester.name if requester else "",
            referee_agreed=bool(r.referee_consent_at), verification_method=r.verification_method,
            form_status=r.form_status, created_at=r.created_at, expires_at=r.expires_at,
        ))
    return out


@router.post("/reference-requests/{public_id}/bank-response", response_model=BankResponseResult, tags=["Bank responses"])
def bank_response(
    public_id: str,
    body: BankResponseBody,
    institution: Institution = Depends(get_current_institution),
    session: Session = Depends(get_session),
):
    """Answer a Path B request as the referee's bank. Only the responding bank can call this."""
    req = svc.get_request_by_public_id(session, public_id)
    req = svc.submit_bank_response(session, req, institution, body)
    return BankResponseResult(public_id=req.public_id, status=req.status, assurance_level=req.assurance_level)


@router.post("/reference-requests/{public_id}/signed-form", response_model=SignedFormResult, tags=["Signed forms"])
def upload_signed_form(
    public_id: str,
    file: UploadFile = File(...),
    institution: Institution = Depends(get_current_institution),
    session: Session = Depends(get_session),
):
    """Attach the signed bank reference form (JPG, PNG or PDF, up to 5 MB) to a request whose
    referee's bank is on Fidra. Send it again to replace an unclear copy."""
    req = svc.get_request_by_public_id(session, public_id)
    data = file.file.read(svc.MAX_FORM_BYTES + 1)
    svc.attach_signed_form(session, req, institution, file.filename or "signed-form", data)
    return SignedFormResult(public_id=req.public_id, status=req.status, form_status=req.form_status)


@router.get(
    "/reference-requests/{public_id}/signed-form",
    tags=["Signed forms"],
    response_class=Response,
    responses={200: {"content": {"image/jpeg": {}, "image/png": {}, "application/pdf": {}}}},
)
def download_signed_form(
    public_id: str,
    institution: Institution = Depends(get_current_institution),
    session: Session = Depends(get_session),
):
    """Download the signed form. Only the requesting bank and the referee's bank can open it, and every view is logged."""
    req = svc.get_request_by_public_id(session, public_id)
    form = svc.get_signed_form(session, req, institution)
    return Response(content=form.data, media_type=form.content_type,
                    headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
                             "Content-Disposition": f'attachment; filename="signed-form-{public_id}"'})
