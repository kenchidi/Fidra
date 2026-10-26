from sqlmodel import SQLModel, create_engine, Session
from app.core.config import get_settings

settings = get_settings()

_url = settings.database_url
# Hosts hand out "postgres://" or "postgresql://". Newer SQLAlchemy would then look for a different
# driver (psycopg 3) than the one in requirements.txt (psycopg2), so name the driver explicitly.
for _old in ("postgres://", "postgresql://"):
    if _url.startswith(_old):
        _url = "postgresql+psycopg2://" + _url[len(_old):]
        break

# Postgres: give up on an unreachable server after 10 seconds and on a blocked lock after 20, so a
# problem shows up as a clear error in the deploy log instead of a startup that never finishes.
_connect_args = (
    {"check_same_thread": False}
    if _url.startswith("sqlite")
    else {"connect_timeout": 10, "options": "-c lock_timeout=20000 -c statement_timeout=120000"}
)

engine = create_engine(
    _url,
    echo=settings.debug,
    connect_args=_connect_args,
    pool_pre_ping=True,
)


def create_db_and_tables():
    SQLModel.metadata.create_all(engine)


def check_schema():
    """Fail clearly if the tables were created by an older version of Fidra."""
    from sqlalchemy import inspect

    insp = inspect(engine)
    missing = []
    for table in SQLModel.metadata.sorted_tables:
        if not insp.has_table(table.name):
            continue
        have = {c["name"] for c in insp.get_columns(table.name)}
        missing += [f"{table.name}.{c.name}" for c in table.columns if c.name not in have]
    if missing:
        raise RuntimeError(
            "The database tables are from an older version of Fidra. Missing columns: "
            + ", ".join(missing)
            + ". Start from an empty database (or add these columns) and deploy again."
        )


def get_session():
    with Session(engine) as session:
        yield session
