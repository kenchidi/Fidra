import hmac
import secrets
import time
from datetime import timedelta
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlmodel import Session, select

from app.core.database import get_session
from app.core.security import hash_password, verify_password
from app.models.models import Institution, ReferenceRequest, RequestStatus, StaffUser
from app.models.schemas import BankResponseBody, CreateReferenceRequest
from app.services import reference_service as svc

router = APIRouter(prefix="/app", include_in_schema=False)

_env = Environment(
    loader=FileSystemLoader(str(Path(__file__).resolve().parent.parent / "templates")),
    autoescape=select_autoescape(["html", "xml"]),
)


def _wat(dt):
    return (dt + timedelta(hours=1)).strftime("%d %b %Y, %H:%M") if dt else ""


_env.filters["wat"] = _wat
_env.filters["nice"] = lambda v: str(v or "").replace("_", " ").capitalize()

_FAILS: dict = {}  # login throttle: key -> [timestamps]


def csrf(request: Request) -> str:
    tok = request.session.get("csrf")
    if not tok:
        tok = secrets.token_urlsafe(24)
        request.session["csrf"] = tok
    return tok


def check_csrf(request: Request, token: str) -> None:
    if not token or not hmac.compare_digest(token, request.session.get("csrf", "")):
        raise HTTPException(status_code=400, detail="Your session expired. Go back, reload the page and try again.")


def page(name: str, request: Request, status_code: int = 200, **ctx) -> HTMLResponse:
    return HTMLResponse(_env.get_template(name).render(csrf=csrf(request), **ctx), status_code=status_code)


def current_staff(request: Request, session: Session = Depends(get_session)) -> StaffUser:
    uid = request.session.get("staff_id")
    user = session.get(StaffUser, uid) if uid else None
    if not user or not user.is_active:
        request.session.clear()
        raise HTTPException(status_code=303, headers={"Location": "/app/login"})
    if user.must_change_password and request.url.path not in ("/app/account", "/app/account/password", "/app/logout"):
        raise HTTPException(status_code=303, headers={"Location": "/app/account"})
    return user


def ctx_for(staff: StaffUser, session: Session, tab: str) -> dict:
    inst = session.get(Institution, staff.institution_id)
    return {
        "staff": {"full_name": staff.full_name, "role": staff.role, "institution": inst.name},
        "tab": tab,
        "inbox_count": len(svc.pending_for_bank(session, staff.institution_id)),
    }


def friendly(status) -> tuple:
    s = getattr(status, "value", status)
    if s in ("authorized", "completed"):
        return "Confirmed", "ok"
    if s == "declined":
        return "Referee declined", "bad"
    if s in ("expired", "cancelled", "failed"):
        return "Didn't go through", "bad"
    if s in ("draft", "pending_consent"):
        return "Not sent yet", "neu"
    return "Waiting for referee", "warn"


EVENT_LABELS = {
    "submitted": "Request sent", "referee_notified": "Referee link created", "referee_viewed": "Referee opened the link",
    "pending_bank_confirmation": "Waiting for the referee's bank", "authorized": "Referee confirmed",
    "completed": "Completed", "declined": "Referee declined", "expired": "Request expired",
    "cancelled": "Request cancelled", "failed": "Request failed",
}


def event_label(e) -> str:
    note = e.note or ""
    if note.startswith("Signed form uploaded"):
        return "Signed form sent to the referee's bank"
    if note.startswith("New copy"):
        return "A clearer copy of the form was sent"
    if "asked for a clearer copy" in note:
        return "The referee's bank asked for a clearer copy"
    if note.startswith("Signature did not match"):
        return "The bank couldn't match the signature"
    if note.startswith("Referee agreed"):
        return "Referee agreed to the check"
    if note.endswith("confirmed the account"):
        return "The referee's bank confirmed the account"
    if note.startswith("Account not found"):
        return "The bank couldn't find the account"
    return EVENT_LABELS.get(e.to_status, e.to_status)


def banner(req: ReferenceRequest) -> dict:
    s = getattr(req.status, "value", req.status)
    pl = req.response_payload or {}
    if s == "failed" and pl.get("signature_match") == "mismatch":
        return {"tone": "bad", "title": "The referee's bank could not match the signature", "sub": "The signature on the form did not match the bank's records. Check the form with the referee and start a new request.", "icon": "x"}
    if s == "pending_bank_confirmation" and req.form_status == "unclear":
        return {"tone": "warn", "title": "The referee's bank couldn't read the form", "sub": "Upload a clearer photo or scan below.", "icon": "clock"}
    if s == "failed" and (req.response_payload or {}).get("account_exists") is False:
        return {"tone": "bad", "title": "The referee's bank couldn't find that account", "sub": "Check the account number and bank, then start a new request.", "icon": "x"}
    bank = req.assurance_level == "bank_confirmed"
    if s in ("authorized", "completed"):
        if bank:
            sub = f"Signature compared by {pl.get('responded_by_name') or 'the bank'}" if req.verification_method == "signed_form" else None
            return {"tone": "ok", "title": "Confirmed by the referee's bank", "sub": sub, "icon": "bank"}
        return {"tone": "ok", "title": "Confirmed by the referee", "sub": "Not checked by a bank. This is the referee's own answer.", "icon": "person"}
    if s == "declined":
        return {"tone": "bad", "title": "The referee declined", "icon": "x"}
    if s in ("expired", "cancelled", "failed"):
        return {"tone": "bad", "title": "This request didn't go through", "icon": "x"}
    if s == "pending_bank_confirmation":
        return {"tone": "warn", "title": "Waiting for the referee's bank", "icon": "clock"}
    return {"tone": "warn", "title": "Waiting for the referee", "icon": "clock"}


def confirmed_items(p: dict, bank: bool) -> list:
    if not p:
        return []
    yes = lambda v: str(v).lower() in ("true", "yes")
    items = []
    if p.get("signature_match") == "matches":
        items.append(("Signature matches the bank's records", True))
    if bank:
        for key, label in (("account_exists", "Referee's account exists"), ("account_active", "Account is active"),
                           ("account_name_match", "Name on the account matches")):
            if p.get(key) is not None:
                items.append((label, yes(p[key])))
    if p.get("good_standing") is not None:
        items.append(("Referee says the account is in good standing" if not bank else "Referee is in good standing", yes(p["good_standing"])))
    return items


def _fmt_req(r: ReferenceRequest) -> dict:
    label, tone = friendly(r.status)
    return {"id": r.public_id, "name": r.customer_full_name, "purpose": r.purpose, "label": label, "tone": tone,
            "bank": r.assurance_level == "bank_confirmed", "created": r.created_at}


def _form_info(session: Session, req: ReferenceRequest):
    f = svc.latest_form(session, req)
    if not f:
        return None
    return {"name": f.filename, "pdf": f.content_type == "application/pdf", "at": f.uploaded_at, "removed": f.data is None}


def _responder_name(session: Session, req: ReferenceRequest) -> str:
    inst = session.get(Institution, req.responding_institution_id) if req.responding_institution_id else None
    return inst.name if inst else "the referee's bank"


# ---------------- auth
@router.get("")
def root():
    return RedirectResponse("/app/requests", status_code=303)


@router.get("/login", response_class=HTMLResponse)
def login_form(request: Request):
    return page("dash_login.html", request, error=None)


@router.post("/login", response_class=HTMLResponse)
def login(request: Request, email: str = Form(""), password: str = Form(""), csrf_token: str = Form("", alias="csrf"),
          session: Session = Depends(get_session)):
    check_csrf(request, csrf_token)
    email = email.strip().lower()
    key = f"{email}|{request.client.host if request.client else ''}"
    now = time.time()
    recent = [t for t in _FAILS.get(key, []) if now - t < 600]
    if len(recent) >= 5:
        return page("dash_login.html", request, 429, error="Too many attempts. Try again in 10 minutes.")
    user = session.exec(select(StaffUser).where(StaffUser.email == email)).first()
    if not user or not user.is_active or not verify_password(password, user.password_hash):
        _FAILS[key] = recent + [now]
        return page("dash_login.html", request, 401, error="That email or password isn't right.")
    _FAILS.pop(key, None)
    request.session.clear()
    request.session["staff_id"] = user.id
    return RedirectResponse("/app/requests", status_code=303)


@router.post("/logout")
def logout(request: Request, csrf_token: str = Form("", alias="csrf")):
    check_csrf(request, csrf_token)
    request.session.clear()
    return RedirectResponse("/app/login", status_code=303)


# ---------------- requests
@router.get("/requests", response_class=HTMLResponse)
def requests_list(request: Request, q: str = "", staff: StaffUser = Depends(current_staff), session: Session = Depends(get_session)):
    stmt = select(ReferenceRequest).where(ReferenceRequest.requesting_institution_id == staff.institution_id)
    if q.strip():
        stmt = stmt.where(ReferenceRequest.customer_full_name.ilike(f"%{q.strip()}%"))
    rows = session.exec(stmt.order_by(ReferenceRequest.created_at.desc()).limit(100)).all()
    return page("dash_requests.html", request, rows=[_fmt_req(r) for r in rows], q=q, **ctx_for(staff, session, "list"))


def _bank_options(session: Session) -> list:
    insts = session.exec(select(Institution).where(Institution.is_active == True, Institution.bank_code != None)).all()  # noqa: E711
    return sorted(({"code": i.bank_code, "name": i.name, "net": i.network_connected} for i in insts), key=lambda x: x["name"])


@router.get("/requests/new", response_class=HTMLResponse)
def new_form(request: Request, staff: StaffUser = Depends(current_staff), session: Session = Depends(get_session)):
    return page("dash_new.html", request, banks=_bank_options(session), form={}, error=None, **ctx_for(staff, session, "new"))


@router.post("/requests/new", response_class=HTMLResponse)
def new_submit(
    request: Request,
    customer_full_name: str = Form(""), customer_phone: str = Form(""), purpose: str = Form("account_opening"),
    referee_account_number: str = Form(""), referee_bank: str = Form(""), other_bank_code: str = Form(""),
    consent: str = Form(""), confirm_method: str = Form("link"), form_checked: str = Form(""),
    form_file: UploadFile = File(None), csrf_token: str = Form("", alias="csrf"),
    staff: StaffUser = Depends(current_staff), session: Session = Depends(get_session),
):
    check_csrf(request, csrf_token)
    form = dict(customer_full_name=customer_full_name, customer_phone=customer_phone, purpose=purpose,
                referee_account_number=referee_account_number, referee_bank=referee_bank, other_bank_code=other_bank_code,
                confirm_method=confirm_method)
    bank_code = (other_bank_code if referee_bank == "__other" else referee_bank).strip()
    acct = referee_account_number.strip()
    error = None
    if not customer_full_name.strip():
        error = "Enter the applicant's full name."
    elif not (acct.isdigit() and len(acct) == 10):
        error = "The referee's account number should have 10 digits."
    elif not bank_code:
        error = "Choose the referee's bank."
    elif not consent:
        error = "Confirm that the customer has agreed to this reference check."
    use_form = confirm_method == "form"
    data = b""
    if use_form and not error:
        connected = session.exec(select(Institution).where(Institution.bank_code == bank_code, Institution.network_connected == True,  # noqa: E712
                                                           Institution.is_active == True)).first()
        if not connected:
            error = "The signed-form route is only for banks on Fidra. Choose \u201cSend a link\u201d instead."
        else:
            data = form_file.file.read(svc.MAX_FORM_BYTES + 1) if form_file is not None and form_file.filename else b""
            if not data:
                error = "Attach a photo or scan of the signed form."
            elif not form_checked:
                error = "Confirm that you have checked the form is complete and signed."
            else:
                try:
                    svc.validate_form_bytes(data)
                except HTTPException as e:
                    error = e.detail
    if error:
        return page("dash_new.html", request, 422, banks=_bank_options(session), form=form, error=error, **ctx_for(staff, session, "new"))
    inst = session.get(Institution, staff.institution_id)
    body = CreateReferenceRequest(customer_full_name=customer_full_name.strip(), customer_phone=customer_phone.strip() or None,
                                  referee_account_number=acct, referee_bank_code=bank_code, purpose=purpose, consent_given=True)
    req = svc.create_reference_request(session, body, inst, base_url=str(request.base_url).rstrip("/"))
    if use_form:
        svc.attach_signed_form(session, req, inst, form_file.filename, data, staff_id=staff.id)
    return RedirectResponse(f"/app/requests/{req.public_id}", status_code=303)


@router.get("/requests/{public_id}", response_class=HTMLResponse)
def detail(public_id: str, request: Request, print: int = 0, err: str = "", staff: StaffUser = Depends(current_staff), session: Session = Depends(get_session)):
    req = session.exec(select(ReferenceRequest).where(ReferenceRequest.public_id == public_id)).first()
    if not req or req.requesting_institution_id != staff.institution_id:
        raise HTTPException(status_code=404, detail="Request not found")
    base = str(request.base_url).rstrip("/")
    s = getattr(req.status, "value", req.status)
    events = svc.get_history(session, req.id)
    history = [{"label": event_label(e), "at": e.created_at} for e in events]
    declined = next((e.note for e in events if e.to_status == "declined" and e.note and e.note != "Declined by referee"), None)
    waiting = (req.path == "path_a" and s in ("referee_notified", "referee_viewed", "submitted")) or (
        req.path == "path_b" and s == "pending_bank_confirmation" and not req.referee_consent_at
    )
    link = f"{base}/r/{req.referee_token}"
    wa = "https://wa.me/?text=" + quote(f"Please confirm a reference for {req.customer_full_name}: {link}")
    payload = req.response_payload or {}
    return page("dash_detail.html", request, r=req, view=banner(req), history=history, print=bool(print),
                items=confirmed_items(payload, req.assurance_level == "bank_confirmed"),
                relation=payload.get("relationship_length_band") or payload.get("relationship"),
                comment=payload.get("additional_comments"), decline_reason=declined,
                share=(link if waiting else None), wa=wa,
                is_b=req.path == "path_b", agreed=bool(req.referee_consent_at), bank_done=bool(req.bank_answer),
                signed=req.verification_method == "signed_form", unclear=req.form_status == "unclear",
                form_info=_form_info(session, req), form_error=err[:200], responder=_responder_name(session, req),
                customer_link=f"{base}/c/{req.customer_token}", **ctx_for(staff, session, "list"))


# ---------------- inbox (staff at the referee's bank)
def _inbox_req(session: Session, staff: StaffUser, public_id: str) -> ReferenceRequest:
    req = session.exec(select(ReferenceRequest).where(ReferenceRequest.public_id == public_id)).first()
    if not req or req.path != "path_b" or req.responding_institution_id != staff.institution_id:
        raise HTTPException(status_code=404, detail="Request not found")
    return req


@router.get("/inbox", response_class=HTMLResponse)
def inbox_list(request: Request, done: int = 0, staff: StaffUser = Depends(current_staff), session: Session = Depends(get_session)):
    rows = []
    for r in svc.pending_for_bank(session, staff.institution_id):
        by = session.get(Institution, r.requesting_institution_id)
        rows.append({"id": r.public_id, "name": r.customer_full_name, "by": by.name if by else "", "account": r.referee_account_number,
                     "agreed": bool(r.referee_consent_at), "created": r.created_at,
                     "form": r.verification_method == "signed_form", "unclear": r.form_status == "unclear"})
    return page("dash_inbox.html", request, rows=rows, done=bool(done), **ctx_for(staff, session, "inbox"))


def _inbox_page(request, staff, session, req, status_code=200, error=None):
    by = session.get(Institution, req.requesting_institution_id)
    return page("dash_inbox_detail.html", request, status_code, r=req, by=by.name if by else "another bank", error=error,
                closed=req.status != RequestStatus.pending_bank_confirmation, form_info=_form_info(session, req),
                signed=req.verification_method == "signed_form", unclear=req.form_status == "unclear", **ctx_for(staff, session, "inbox"))


@router.get("/inbox/{public_id}", response_class=HTMLResponse)
def inbox_detail(public_id: str, request: Request, staff: StaffUser = Depends(current_staff), session: Session = Depends(get_session)):
    return _inbox_page(request, staff, session, _inbox_req(session, staff, public_id))


@router.post("/inbox/{public_id}", response_class=HTMLResponse)
def inbox_submit(
    public_id: str, request: Request, decision: str = Form(""), signature: str = Form(""), account_active: str = Form("yes"),
    good_standing: str = Form("yes"), relationship: str = Form(""), comments: str = Form(""), csrf_token: str = Form("", alias="csrf"),
    staff: StaffUser = Depends(current_staff), session: Session = Depends(get_session),
):
    check_csrf(request, csrf_token)
    req = _inbox_req(session, staff, public_id)
    signed = req.verification_method == "signed_form"
    if signed:
        if signature not in ("matches", "mismatch", "unclear"):
            return _inbox_page(request, staff, session, req, 422, "Say whether the signature matches your records.")
        if signature == "matches" and decision not in ("confirm", "not_found"):
            return _inbox_page(request, staff, session, req, 422, "Choose whether you found the account.")
        found = decision != "not_found"
    else:
        if decision not in ("confirm", "not_found"):
            return _inbox_page(request, staff, session, req, 422, "Choose whether you found the account.")
        found = decision == "confirm"
    body = BankResponseBody(account_exists=found, account_active=found and account_active == "yes", good_standing=found and good_standing == "yes",
                            relationship_length_band=(relationship or None) if found else None, comments=comments.strip() or None,
                            signature_match=signature if signed else None)
    try:
        svc.submit_bank_response(session, req, session.get(Institution, staff.institution_id), body)
    except HTTPException as e:
        return _inbox_page(request, staff, session, req, 400, e.detail)
    return RedirectResponse("/app/inbox?done=1", status_code=303)


# ---------------- signed forms
@router.get("/forms/{public_id}")
def view_form(public_id: str, staff: StaffUser = Depends(current_staff), session: Session = Depends(get_session)):
    req = session.exec(select(ReferenceRequest).where(ReferenceRequest.public_id == public_id)).first()
    if not req:
        raise HTTPException(status_code=404, detail="Not found")
    form = svc.get_signed_form(session, req, session.get(Institution, staff.institution_id), staff_id=staff.id)
    disposition = "inline" if form.content_type.startswith("image/") else "attachment"
    return Response(content=form.data, media_type=form.content_type,
                    headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
                             "Content-Disposition": f'{disposition}; filename="signed-form-{public_id}"'})


@router.post("/requests/{public_id}/form")
def replace_form(public_id: str, request: Request, form_file: UploadFile = File(None), csrf_token: str = Form("", alias="csrf"),
                 staff: StaffUser = Depends(current_staff), session: Session = Depends(get_session)):
    check_csrf(request, csrf_token)
    req = session.exec(select(ReferenceRequest).where(ReferenceRequest.public_id == public_id)).first()
    if not req or req.requesting_institution_id != staff.institution_id:
        raise HTTPException(status_code=404, detail="Request not found")
    try:
        data = form_file.file.read(svc.MAX_FORM_BYTES + 1) if form_file is not None and form_file.filename else b""
        svc.attach_signed_form(session, req, session.get(Institution, staff.institution_id), form_file.filename if form_file else "", data, staff_id=staff.id)
    except HTTPException as e:
        return RedirectResponse(f"/app/requests/{public_id}?err={quote(str(e.detail))}", status_code=303)
    return RedirectResponse(f"/app/requests/{public_id}", status_code=303)


# ---------------- own account: change password
@router.get("/account", response_class=HTMLResponse)
def account(request: Request, staff: StaffUser = Depends(current_staff), session: Session = Depends(get_session)):
    return page("dash_account.html", request, error=None, ok=None, me=staff, **ctx_for(staff, session, "account"))


@router.post("/account/password", response_class=HTMLResponse)
def change_password(
    request: Request, current_password: str = Form(""), new_password: str = Form(""), confirm_password: str = Form(""),
    csrf_token: str = Form("", alias="csrf"), staff: StaffUser = Depends(current_staff), session: Session = Depends(get_session),
):
    check_csrf(request, csrf_token)
    key, now = f"pw|{staff.id}", time.time()
    recent = [t for t in _FAILS.get(key, []) if now - t < 600]
    error = ok = None
    if len(recent) >= 5:
        error = "Too many attempts. Try again in 10 minutes."
    elif not verify_password(current_password, staff.password_hash):
        _FAILS[key] = recent + [now]
        error = "Your current password isn't right."
    elif len(new_password) < 10:
        error = "Your new password needs at least 10 characters."
    elif new_password != confirm_password:
        error = "The two new passwords don't match."
    elif new_password == current_password:
        error = "Choose a password you haven't used here before."
    else:
        staff.password_hash = hash_password(new_password)
        staff.must_change_password = False
        session.add(staff)
        session.commit()
        _FAILS.pop(key, None)
        ok = "Your password has been changed."
    return page("dash_account.html", request, 422 if error else 200, error=error, ok=ok, me=staff, **ctx_for(staff, session, "account"))


# ---------------- team (administrators only)
def admin_required(staff: StaffUser = Depends(current_staff)) -> StaffUser:
    if staff.role != "admin":
        raise HTTPException(status_code=403, detail="Only administrators can manage the team.")
    return staff


def _team_page(request, staff, session, error=None, ok=None, status_code=200):
    members = session.exec(select(StaffUser).where(StaffUser.institution_id == staff.institution_id).order_by(StaffUser.full_name)).all()
    return page("dash_team.html", request, status_code, members=members, me=staff.id, error=error, ok=ok, **ctx_for(staff, session, "team"))


def _member(session: Session, staff: StaffUser, uid: int) -> StaffUser:
    m = session.get(StaffUser, uid)
    if not m or m.institution_id != staff.institution_id:
        raise HTTPException(status_code=404, detail="Staff member not found")
    return m


@router.get("/team", response_class=HTMLResponse)
def team(request: Request, staff: StaffUser = Depends(admin_required), session: Session = Depends(get_session)):
    return _team_page(request, staff, session)


@router.post("/team/add", response_class=HTMLResponse)
def team_add(
    request: Request, email: str = Form(""), full_name: str = Form(""), role: str = Form("officer"), password: str = Form(""),
    csrf_token: str = Form("", alias="csrf"), staff: StaffUser = Depends(admin_required), session: Session = Depends(get_session),
):
    check_csrf(request, csrf_token)
    email = email.strip().lower()
    if "@" not in email or not full_name.strip():
        return _team_page(request, staff, session, error="Enter a name and a valid email.", status_code=422)
    if role not in ("officer", "supervisor", "admin"):
        return _team_page(request, staff, session, error="Choose a role.", status_code=422)
    if len(password) < 10:
        return _team_page(request, staff, session, error="The temporary password needs at least 10 characters.", status_code=422)
    if session.exec(select(StaffUser).where(StaffUser.email == email)).first():
        return _team_page(request, staff, session, error="Someone already has an account with that email.", status_code=409)
    session.add(StaffUser(institution_id=staff.institution_id, email=email, full_name=full_name.strip(), role=role,
                          password_hash=hash_password(password), must_change_password=True))
    session.commit()
    return _team_page(request, staff, session, ok=f"{full_name.strip()} can now sign in. They will be asked to choose their own password.")


@router.post("/team/{uid}/toggle", response_class=HTMLResponse)
def team_toggle(uid: int, request: Request, csrf_token: str = Form("", alias="csrf"),
                staff: StaffUser = Depends(admin_required), session: Session = Depends(get_session)):
    check_csrf(request, csrf_token)
    m = _member(session, staff, uid)
    if m.id == staff.id:
        return _team_page(request, staff, session, error="You can't switch off your own account.", status_code=400)
    if m.is_active and m.role == "admin":
        others = session.exec(select(StaffUser).where(StaffUser.institution_id == staff.institution_id, StaffUser.role == "admin",
                                                      StaffUser.is_active == True, StaffUser.id != m.id)).all()  # noqa: E712
        if not others:
            return _team_page(request, staff, session, error="Keep at least one active administrator.", status_code=400)
    m.is_active = not m.is_active
    session.add(m)
    session.commit()
    return _team_page(request, staff, session, ok=f"{m.full_name} is now {'active' if m.is_active else 'switched off'}.")


@router.post("/team/{uid}/password", response_class=HTMLResponse)
def team_reset(uid: int, request: Request, password: str = Form(""), csrf_token: str = Form("", alias="csrf"),
               staff: StaffUser = Depends(admin_required), session: Session = Depends(get_session)):
    check_csrf(request, csrf_token)
    m = _member(session, staff, uid)
    if len(password) < 10:
        return _team_page(request, staff, session, error="The temporary password needs at least 10 characters.", status_code=422)
    m.password_hash = hash_password(password)
    m.must_change_password = True
    session.add(m)
    session.commit()
    return _team_page(request, staff, session, ok=f"New temporary password set for {m.full_name}. They must change it when they sign in.")
