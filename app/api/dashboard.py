import hmac
import secrets
import time
from datetime import timedelta
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlmodel import Session, select

from app.core.database import get_session
from app.core.security import verify_password
from app.models.models import Institution, ReferenceRequest, StaffUser
from app.models.schemas import CreateReferenceRequest
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
    return user


def ctx_for(staff: StaffUser, session: Session, tab: str) -> dict:
    inst = session.get(Institution, staff.institution_id)
    return {"staff": {"full_name": staff.full_name, "role": staff.role, "institution": inst.name}, "tab": tab}


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


def banner(req: ReferenceRequest) -> dict:
    s = getattr(req.status, "value", req.status)
    bank = req.assurance_level == "bank_confirmed"
    if s in ("authorized", "completed"):
        if bank:
            return {"tone": "ok", "title": "Confirmed by the referee's bank", "icon": "bank"}
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
    consent: str = Form(""), csrf_token: str = Form("", alias="csrf"),
    staff: StaffUser = Depends(current_staff), session: Session = Depends(get_session),
):
    check_csrf(request, csrf_token)
    form = dict(customer_full_name=customer_full_name, customer_phone=customer_phone, purpose=purpose,
                referee_account_number=referee_account_number, referee_bank=referee_bank, other_bank_code=other_bank_code)
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
    if error:
        return page("dash_new.html", request, 422, banks=_bank_options(session), form=form, error=error, **ctx_for(staff, session, "new"))
    inst = session.get(Institution, staff.institution_id)
    body = CreateReferenceRequest(customer_full_name=customer_full_name.strip(), customer_phone=customer_phone.strip() or None,
                                  referee_account_number=acct, referee_bank_code=bank_code, purpose=purpose, consent_given=True)
    req = svc.create_reference_request(session, body, inst, base_url=str(request.base_url).rstrip("/"))
    return RedirectResponse(f"/app/requests/{req.public_id}", status_code=303)


@router.get("/requests/{public_id}", response_class=HTMLResponse)
def detail(public_id: str, request: Request, print: int = 0, staff: StaffUser = Depends(current_staff), session: Session = Depends(get_session)):
    req = session.exec(select(ReferenceRequest).where(ReferenceRequest.public_id == public_id)).first()
    if not req or req.requesting_institution_id != staff.institution_id:
        raise HTTPException(status_code=404, detail="Request not found")
    base = str(request.base_url).rstrip("/")
    s = getattr(req.status, "value", req.status)
    events = svc.get_history(session, req.id)
    history = [{"label": EVENT_LABELS.get(e.to_status, e.to_status), "at": e.created_at} for e in events]
    declined = next((e.note for e in events if e.to_status == "declined" and e.note and e.note != "Declined by referee"), None)
    waiting = s in ("referee_notified", "referee_viewed", "submitted")
    link = f"{base}/r/{req.referee_token}"
    wa = "https://wa.me/?text=" + quote(f"Please confirm a reference for {req.customer_full_name}: {link}")
    payload = req.response_payload or {}
    return page("dash_detail.html", request, r=req, view=banner(req), history=history, print=bool(print),
                items=confirmed_items(payload, req.assurance_level == "bank_confirmed"),
                relation=payload.get("relationship_length_band") or payload.get("relationship"),
                comment=payload.get("additional_comments"), decline_reason=declined,
                share=(link if (req.path == "path_a" and waiting) else None), wa=wa,
                customer_link=f"{base}/c/{req.customer_token}", **ctx_for(staff, session, "list"))
