from typing import Optional, Tuple
from sqlmodel import Session

from app.models.models import Institution, new_api_key


def create_institution(
    session: Session,
    *,
    code: str,
    name: str,
    bank_code: Optional[str] = None,
    network_connected: bool = False,
) -> Tuple[Institution, str]:
    """Create an institution and return it together with its plain API key (shown once)."""
    plain, key_hash = new_api_key()
    inst = Institution(
        code=code,
        name=name,
        bank_code=bank_code,
        network_connected=network_connected,
        api_key_hash=key_hash,
        api_key_prefix=plain[:7],
    )
    session.add(inst)
    session.commit()
    session.refresh(inst)
    return inst, plain
