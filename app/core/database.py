from sqlmodel import SQLModel, create_engine, Session
from app.core.config import get_settings

settings = get_settings()

_url = settings.database_url
if _url.startswith("postgres://"):  # some hosts still hand out the old scheme
    _url = _url.replace("postgres://", "postgresql://", 1)

_connect_args = {"check_same_thread": False} if _url.startswith("sqlite") else {}

engine = create_engine(
    _url,
    echo=settings.debug,
    connect_args=_connect_args,
    pool_pre_ping=True,
)


def create_db_and_tables():
    SQLModel.metadata.create_all(engine)


def get_session():
    with Session(engine) as session:
        yield session
