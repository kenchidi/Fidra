from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlmodel import Session, select
from pathlib import Path

from app.core.config import get_settings
from app.core.database import create_db_and_tables, engine
from app.models.models import Institution
from app.services.institution_service import create_institution
from app.services.reference_service import purge_expired_forms
from app.api.routes import router as api_router
from app.api.public import router as public_router
from app.api.dashboard import router as dashboard_router
from starlette.middleware.sessions import SessionMiddleware
import secrets

settings = get_settings()

BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"

jinja_env = Environment(
    loader=FileSystemLoader(str(TEMPLATES_DIR)),
    autoescape=select_autoescape(["html", "xml"]),
)


def render(template_name: str, **context) -> HTMLResponse:
    template = jinja_env.get_template(template_name)
    return HTMLResponse(content=template.render(**context))


def seed_institutions():
    """Seed a few demo institutions so the system is usable immediately."""
    demo = [
        # network_connected=True → Path B (bank-confirmed) available
        {"code": "gtb", "name": "Guaranty Trust Bank", "bank_code": "058", "network_connected": True},
        {"code": "firstbank", "name": "First Bank of Nigeria", "bank_code": "011", "network_connected": True},
        {"code": "zenith", "name": "Zenith Bank", "bank_code": "057", "network_connected": False},
        {"code": "access", "name": "Access Bank", "bank_code": "044", "network_connected": False},
        {"code": "kuda", "name": "Kuda Microfinance Bank", "bank_code": "090267", "network_connected": False},
        {"code": "opay", "name": "OPay Digital Services", "bank_code": "100004", "network_connected": False},
    ]
    with Session(engine) as session:
        print("\n=== Fidra Demo Institutions (development only) ===")
        for item in demo:
            existing = session.exec(
                select(Institution).where(Institution.code == item["code"])
            ).first()
            if existing:
                print(f"  {existing.name:30} | code={existing.code:12} | key already issued")
                continue
            inst, plain = create_institution(session, **item)
            print(f"  {inst.name:30} | code={inst.code:12} | API Key: {plain}")
        print("===================================================\n")


@asynccontextmanager
async def lifespan(app: FastAPI):
    create_db_and_tables()
    with Session(engine) as _s:
        purge_expired_forms(_s)
    if settings.seed_demo_data:
        seed_institutions()
    yield


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="Fidra — The rail that moves trust between institutions.",
    lifespan=lifespan,
    docs_url=None,       # disable default Swagger UI
    redoc_url=None,      # disable ReDoc
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(
    SessionMiddleware,
    secret_key=settings.session_secret or secrets.token_urlsafe(32),
    session_cookie="fidra_session",
    https_only=settings.session_https_only,
    same_site="lax",
    max_age=60 * 60 * 8,
)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

app.include_router(api_router, prefix="/v1")
app.include_router(public_router)
app.include_router(dashboard_router)


@app.get("/", response_class=HTMLResponse)
def homepage(request: Request):
    return render("home.html", request=request)


@app.get("/docs", include_in_schema=False)
def custom_docs():
    """Serve the Fidra-branded API documentation."""
    return FileResponse(STATIC_DIR / "fidra-docs.html")


@app.get("/health")
def health():
    return {"status": "ok", "name": "Fidra"}
