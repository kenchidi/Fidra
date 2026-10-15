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
from app.api.routes import router as api_router
from app.api.public import router as public_router

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
        {"code": "gtb", "name": "Guaranty Trust Bank", "bank_code": "058"},
        {"code": "firstbank", "name": "First Bank of Nigeria", "bank_code": "011"},
        {"code": "zenith", "name": "Zenith Bank", "bank_code": "057"},
        {"code": "access", "name": "Access Bank", "bank_code": "044"},
        {"code": "kuda", "name": "Kuda Microfinance Bank", "bank_code": "090267"},
        {"code": "opay", "name": "OPay Digital Services", "bank_code": "100004"},
    ]
    with Session(engine) as session:
        for item in demo:
            existing = session.exec(
                select(Institution).where(Institution.code == item["code"])
            ).first()
            if not existing:
                session.add(Institution(**item))
        session.commit()

        print("\n=== Fidra Demo Institutions ===")
        institutions = session.exec(select(Institution)).all()
        for inst in institutions:
            print(f"  {inst.name:30} | code={inst.code:12} | API Key: {inst.api_key}")
        print("================================\n")


@asynccontextmanager
async def lifespan(app: FastAPI):
    create_db_and_tables()
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
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

app.include_router(api_router, prefix="/v1")
app.include_router(public_router)


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
