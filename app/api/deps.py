from fastapi import Depends, HTTPException, Header
from sqlmodel import Session, select
from typing import Optional

from app.core.database import get_session
from app.models.models import Institution


def get_current_institution(
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
    session: Session = Depends(get_session),
) -> Institution:
    if not x_api_key:
        raise HTTPException(status_code=401, detail="Missing X-API-Key header")

    institution = session.exec(
        select(Institution).where(Institution.api_key == x_api_key, Institution.is_active == True)
    ).first()

    if not institution:
        raise HTTPException(status_code=401, detail="Invalid API key")

    return institution
