import hmac
from fastapi import Depends, HTTPException, Header
from sqlmodel import Session, select
from typing import Optional

from app.core.config import get_settings
from app.core.database import get_session
from app.models.models import Institution, hash_api_key


def get_current_institution(
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
    session: Session = Depends(get_session),
) -> Institution:
    if not x_api_key:
        raise HTTPException(status_code=401, detail="Missing X-API-Key header")

    institution = session.exec(
        select(Institution).where(
            Institution.api_key_hash == hash_api_key(x_api_key),
            Institution.is_active == True,
        )
    ).first()

    if not institution:
        raise HTTPException(status_code=401, detail="Invalid API key")

    return institution


def require_admin(x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key")) -> None:
    expected = get_settings().admin_api_key
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="Institution creation is disabled. Set ADMIN_API_KEY on the server.",
        )
    if not x_admin_key or not hmac.compare_digest(x_admin_key, expected):
        raise HTTPException(status_code=401, detail="Invalid admin key")
