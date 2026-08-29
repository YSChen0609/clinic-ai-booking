"""FastAPI entry: pages, health, session auth, booking API, and chat agent."""

from collections.abc import AsyncIterator, Generator
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
import logging
import os

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware

from clinic_ai_booking.agent import (
    SESSION_THREAD_KEY,
    SESSION_VISITOR_EMAIL_KEY,
    SESSION_VISITOR_NAME_KEY,
    new_thread_id,
    run_chat_turn,
)
from clinic_ai_booking.auth import (
    LOGIN_REQUIRED_MESSAGE,
    SESSION_USER_ID_KEY,
    AuthError,
    cancel_for_user,
    get_user_by_id,
    login_with_name_email,
    reschedule_for_user,
)
from clinic_ai_booking.booking import BookingError, book_appointment
from clinic_ai_booking.chat_context import ChatContext
from clinic_ai_booking.db import apply_schema_and_seed, database_url_from_env
from clinic_ai_booking.doctors import DOCTORS, DOCTORS_BY_SLUG
from clinic_ai_booking.hours import TIMEZONE_NAME
from clinic_ai_booking.models import User

logger = logging.getLogger(__name__)

PACKAGE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = PACKAGE_DIR / "templates"
STATIC_DIR = PACKAGE_DIR / "static"

# MVP local default only — set SESSION_SECRET in real deploys.
_SESSION_SECRET = os.environ.get("SESSION_SECRET", "clinic-dev-session-secret-change-me")

_engine: Engine | None = None


def get_engine() -> Engine | None:
    """Return the process engine after lifespan init, if any."""
    return _engine


def set_engine(engine: Engine | None) -> None:
    """Bind or clear the process engine (tests may override)."""
    global _engine
    _engine = engine


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Apply booking schema and seed catalog when DATABASE_URL is set."""
    package_log = logging.getLogger("clinic_ai_booking")
    package_log.setLevel(logging.INFO)
    if not package_log.handlers:
        handler = logging.StreamHandler()
        handler.setLevel(logging.INFO)
        handler.setFormatter(logging.Formatter("%(levelname)s:%(name)s:%(message)s"))
        package_log.addHandler(handler)
        package_log.propagate = False
    url = database_url_from_env()
    if url:
        set_engine(apply_schema_and_seed(url))
    yield
    set_engine(None)


app = FastAPI(title="clinic-ai-booking", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    SessionMiddleware,
    secret_key=_SESSION_SECRET,
    session_cookie="clinic_session",
    same_site="lax",
    https_only=False,
)
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def get_db() -> Generator[Session, None, None]:
    """Yield a DB session bound to the app engine."""
    engine = get_engine()
    if engine is None:
        raise HTTPException(status_code=503, detail="database is not configured")
    session = Session(engine)
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def current_user(request: Request, db: Session = Depends(get_db)) -> User | None:
    """Load the logged-in user from the signed session cookie, if any."""
    raw = request.session.get(SESSION_USER_ID_KEY)
    if raw is None:
        return None
    try:
        user_id = int(raw)
    except (TypeError, ValueError):
        return None
    return get_user_by_id(db, user_id)


def optional_user(request: Request) -> User | None:
    """Resolve session user for HTML pages when the DB may be unavailable."""
    engine = get_engine()
    if engine is None:
        return None
    raw = request.session.get(SESSION_USER_ID_KEY)
    if raw is None:
        return None
    try:
        user_id = int(raw)
    except (TypeError, ValueError):
        return None
    with Session(engine) as session:
        user = get_user_by_id(session, user_id)
        if user is not None:
            session.expunge(user)
        return user


def _page_context(
    request: Request,
    *,
    title: str,
    user: User | None = None,
    doctor=None,
) -> dict:
    """Shared template vars for intro/doctor pages."""
    return {
        "title": title,
        "doctors": DOCTORS,
        "timezone": TIMEZONE_NAME,
        "user": user,
        "doctor": doctor,
        "login_required_message": LOGIN_REQUIRED_MESSAGE,
    }


@app.get("/health")
def health() -> dict[str, str]:
    """Return service liveness for Compose and smoke checks."""
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
def intro(request: Request) -> HTMLResponse:
    """Render the clinic intro page with doctor links."""
    return templates.TemplateResponse(
        request,
        "intro.html",
        _page_context(request, title="Clinic intro", user=optional_user(request)),
    )


@app.get("/doctors/{slug}", response_class=HTMLResponse)
def doctor_page(request: Request, slug: str) -> HTMLResponse:
    """Render a single doctor placeholder page."""
    doctor = DOCTORS_BY_SLUG.get(slug)
    if doctor is None:
        raise HTTPException(status_code=404, detail="Doctor not found")
    return templates.TemplateResponse(
        request,
        "doctor.html",
        _page_context(
            request,
            title=doctor.name,
            user=optional_user(request),
            doctor=doctor,
        ),
    )


@app.get("/auth/me")
def auth_me(user: User | None = Depends(current_user)) -> dict:
    """Return visitor or the logged-in patient (no password fields)."""
    if user is None:
        return {"authenticated": False, "user": None}
    return {
        "authenticated": True,
        "user": {"id": user.id, "name": user.name, "email": user.email},
    }


class LoginBody(BaseModel):
    name: str = Field(min_length=1)
    email: str = Field(min_length=3)


class BookBody(BaseModel):
    professional_slug: str
    service_code: str
    starts_at: datetime
    patient_name: str | None = None
    patient_email: str | None = None


class RescheduleBody(BaseModel):
    starts_at: datetime


class ChatBody(BaseModel):
    message: str = Field(min_length=1, max_length=2000)


@app.post("/api/chat")
def api_chat(
    request: Request,
    body: ChatBody,
    db: Session = Depends(get_db),
    user: User | None = Depends(current_user),
) -> dict:
    """Run one messenger turn through create_agent (tools + session thread history)."""
    thread_id = request.session.get(SESSION_THREAD_KEY)
    if not isinstance(thread_id, str) or not thread_id:
        thread_id = new_thread_id()
        request.session[SESSION_THREAD_KEY] = thread_id

    visitor_name = request.session.get(SESSION_VISITOR_NAME_KEY)
    visitor_email = request.session.get(SESSION_VISITOR_EMAIL_KEY)
    if user is not None:
        visitor_name = None
        visitor_email = None

    context = ChatContext(
        db=db,
        user_id=user.id if user else None,
        user_name=user.name if user else None,
        user_email=user.email if user else None,
        visitor_name=visitor_name if isinstance(visitor_name, str) else None,
        visitor_email=visitor_email if isinstance(visitor_email, str) else None,
    )
    try:
        reply = run_chat_turn(message=body.message, thread_id=thread_id, context=context)
    except Exception:
        logger.exception("chat turn failed thread_id=%s", thread_id)
        raise HTTPException(
            status_code=502,
            detail="Chat is temporarily unavailable. Is Ollama running with the model pulled?",
        ) from None

    if user is None and context.has_visitor_contact:
        request.session[SESSION_VISITOR_NAME_KEY] = context.visitor_name
        request.session[SESSION_VISITOR_EMAIL_KEY] = context.visitor_email

    return {"reply": reply, "thread_id": thread_id}


@app.post("/auth/login")
def auth_login(
    request: Request,
    db: Session = Depends(get_db),
    name: str = Form(...),
    email: str = Form(...),
) -> RedirectResponse:
    """Log in with name + email (no password v1) and set the session cookie."""
    try:
        user = login_with_name_email(db, name, email)
    except AuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    request.session.clear()
    request.session[SESSION_USER_ID_KEY] = user.id
    return RedirectResponse(url="/", status_code=303)


@app.post("/auth/login.json")
def auth_login_json(
    request: Request,
    body: LoginBody,
    db: Session = Depends(get_db),
) -> dict:
    """JSON login for tests and API clients; sets the same session cookie."""
    try:
        user = login_with_name_email(db, body.name, body.email)
    except AuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    request.session.clear()
    request.session[SESSION_USER_ID_KEY] = user.id
    return {
        "authenticated": True,
        "user": {"id": user.id, "name": user.name, "email": user.email},
    }


@app.post("/auth/logout")
def auth_logout(request: Request) -> RedirectResponse:
    """Clear the session cookie and return to the intro page."""
    request.session.clear()
    return RedirectResponse(url="/", status_code=303)


@app.post("/auth/logout.json")
def auth_logout_json(request: Request) -> dict[str, bool]:
    """Clear the session cookie (JSON clients / tests)."""
    request.session.clear()
    return {"authenticated": False}


@app.post("/api/bookings")
def api_book(
    body: BookBody,
    db: Session = Depends(get_db),
    user: User | None = Depends(current_user),
) -> dict:
    """Book a slot. Visitors must send name+email; logged-in users may omit them."""
    if user is not None:
        name = body.patient_name or user.name
        email = body.patient_email or user.email
    else:
        if not body.patient_name or not body.patient_email:
            raise HTTPException(
                status_code=400,
                detail="name and email are required to book as a visitor",
            )
        name = body.patient_name
        email = body.patient_email
    try:
        booking = book_appointment(
            db,
            professional_slug=body.professional_slug,
            service_code=body.service_code,
            starts_at=body.starts_at,
            patient_name=name,
            patient_email=email,
        )
    except (BookingError, AuthError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "id": booking.id,
        "status": booking.status,
        "patient_email": booking.patient_email,
        "user_id": booking.user_id,
        "starts_at": booking.starts_at.isoformat(),
        "ends_at": booking.ends_at.isoformat(),
    }


@app.post("/api/bookings/{booking_id}/cancel")
def api_cancel(
    booking_id: int,
    db: Session = Depends(get_db),
    user: User | None = Depends(current_user),
) -> dict:
    """Cancel a booking; visitors are told to log in."""
    try:
        booking = cancel_for_user(db, booking_id, user)
    except AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except BookingError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"id": booking.id, "status": booking.status}


@app.post("/api/bookings/{booking_id}/reschedule")
def api_reschedule(
    booking_id: int,
    body: RescheduleBody,
    db: Session = Depends(get_db),
    user: User | None = Depends(current_user),
) -> dict:
    """Reschedule a booking; visitors are told to log in."""
    try:
        booking = reschedule_for_user(db, booking_id, body.starts_at, user)
    except AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except BookingError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "id": booking.id,
        "status": booking.status,
        "starts_at": booking.starts_at.isoformat(),
        "ends_at": booking.ends_at.isoformat(),
    }


def run() -> None:
    """Start the HTTP server (used by the package console script)."""
    import uvicorn

    uvicorn.run(
        "clinic_ai_booking.main:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
    )
