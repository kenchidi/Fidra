from fastapi import APIRouter, Depends, Request, Form
from fastapi.responses import HTMLResponse
from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlmodel import Session
from pathlib import Path

from app.core.database import get_session
from app.services import reference_service as svc
from app.models.models import Institution
from app.models.schemas import ResponsePayload

router = APIRouter()

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
jinja_env = Environment(
    loader=FileSystemLoader(str(TEMPLATES_DIR)),
    autoescape=select_autoescape(["html", "xml"]),
)


def render(template_name: str, **context) -> HTMLResponse:
    template = jinja_env.get_template(template_name)
    html = template.render(**context)
    return HTMLResponse(content=html)


# ---------- Referee pages ----------

@router.get("/r/{token}", response_class=HTMLResponse, tags=["Public"])
def referee_page(token: str, request: Request, session: Session = Depends(get_session)):
    req = svc.get_request_by_referee_token(session, token)
    svc.mark_referee_viewed(session, req)

    return render(
        "referee.html",
        request=request,
        req=req,
        token=token,
        **_names(session, req),
        already_done=req.status.value in ("completed", "declined", "expired", "cancelled"),
    )


@router.post("/r/{token}/authorize", response_class=HTMLResponse, tags=["Public"])
def referee_authorize(
    token: str,
    request: Request,
    session: Session = Depends(get_session),
    good_standing: str = Form("yes"),
    relationship: str = Form("1-3_years"),
    comments: str = Form(""),
):
    req = svc.get_request_by_referee_token(session, token)

    payload = ResponsePayload(
        account_exists=None,        # nobody verified this
        account_name_match=None,
        good_standing=good_standing == "yes",
        relationship_length_band=relationship,
        additional_comments=comments or None,
        responded_by="referee_self",
    )
    svc.authorize_request(session, req, payload)

    return render(
        "referee_done.html",
        request=request,
        req=req,
        action="authorized",
        **_names(session, req),
    )


@router.post("/r/{token}/decline", response_class=HTMLResponse, tags=["Public"])
def referee_decline(
    token: str,
    request: Request,
    session: Session = Depends(get_session),
    reason: str = Form(""),
):
    req = svc.get_request_by_referee_token(session, token)
    svc.decline_request(session, req, reason=reason or None)

    return render(
        "referee_done.html",
        request=request,
        req=req,
        action="declined",
        **_names(session, req),
    )


# ---------- helpers for the referee pages ----------

def _names(session: Session, req) -> dict:
    requester = session.get(Institution, req.requesting_institution_id)
    responder = session.get(Institution, req.responding_institution_id) if req.responding_institution_id else None
    acct = req.referee_account_number or ""
    return {
        "requester": requester.name if requester else "the requesting institution",
        "responder": responder.name if responder else "your bank",
        "masked_account": "\u2022" * max(len(acct) - 4, 0) + acct[-4:],
    }


# ---------- Customer status page ----------

@router.get("/c/{token}", response_class=HTMLResponse, tags=["Public"])
def customer_status(token: str, request: Request, session: Session = Depends(get_session)):
    req = svc.get_request_by_customer_token(session, token)
    history = svc.get_history(session, req.id)

    return render(
        "customer.html",
        request=request,
        req=req,
        history=history,
        token=token,
    )
