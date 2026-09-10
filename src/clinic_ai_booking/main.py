"""FastAPI entry: pages, health, session auth, booking API, chat agent."""

from collections.abc import AsyncIterator, Generator
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
import logging
import os
import uuid
from collections import defaultdict

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware

from clinic_ai_booking.auth import (
    LOGIN_REQUIRED_MESSAGE,
    SESSION_USER_ID_KEY,
    AuthError,
    cancel_for_user,
    get_user_by_id,
    login_with_name_email,
    reschedule_for_user,
)
from clinic_ai_booking.domain.booking import (
    BookingError,
    BusyBlock,
    book_appointment,
    booking_cancelled_dict,
    booking_created_dict,
    booking_rescheduled_dict,
    busy_block_public_dict,
    list_busy_blocks_range,
)
from clinic_ai_booking.chat.agent import ClinicAgent
from clinic_ai_booking.chat.context import clear_chat_booking_state, has_visitor_contact_in_session
from clinic_ai_booking.chat.deps import get_chat_agent
from clinic_ai_booking.chat.factory import create_chat_agent, shutdown_chat_agent
from clinic_ai_booking.chat.responses import faq_label
from clinic_ai_booking.chat.session_keys import SESSION_CHAT_DISPLAY_KEY, SESSION_THREAD_KEY
from clinic_ai_booking.notify.adapters.wiring import close_adapter_http, install_ports_from_env
from clinic_ai_booking.config import ChatSettings, NotifySettings, VoiceSettings
from clinic_ai_booking.db import apply_schema_and_seed, database_url_from_env
from clinic_ai_booking.domain.doctors import DOCTORS, DOCTORS_BY_SLUG
from clinic_ai_booking.domain.hours import TIMEZONE_NAME, clinic_today, is_weekday, to_clinic
from clinic_ai_booking.domain.models import User
from clinic_ai_booking.voice import VoiceError, synthesize_speech, transcribe_audio

# Public doctor calendars show this many days starting at `from`.
_CALENDAR_DAYS = 7
_MAX_BUSY_RANGE_DAYS = 31

logger = logging.getLogger(__name__)

PACKAGE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = PACKAGE_DIR / "templates"
STATIC_DIR = PACKAGE_DIR / "static"

# MVP local default only — set SESSION_SECRET in real deploys.
_SESSION_SECRET = os.environ.get("SESSION_SECRET", "clinic-dev-session-secret-change-me")

_CHAT_UNAVAILABLE = "Chat is temporarily unavailable. Check Ollama and try again."
_VOICE_UNAVAILABLE = "Voice is temporarily unavailable. Check the voice service and try again."
_MAX_VOICE_UPLOAD_BYTES = 25 * 1024 * 1024

_engine: Engine | None = None


def get_engine() -> Engine | None:
    """Return the process engine after lifespan init, if any."""
    return _engine


def set_engine(engine: Engine | None) -> None:
    """Bind or clear the process engine (tests may override)."""
    global _engine
    _engine = engine


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Apply booking schema, seed catalog, and start the chat agent."""
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

    settings = ChatSettings.from_env()
    app.state.chat_settings = settings
    voice_settings = VoiceSettings.from_env()
    app.state.voice_settings = voice_settings
    notify_settings = NotifySettings.from_env()
    app.state.notify_settings = notify_settings
    install_ports_from_env(notify_settings)
    if settings.chat_enabled:
        app.state.chat_agent = create_chat_agent(settings=settings)
        logger.info("chat agent started model=%s", settings.ollama_model)
    else:
        app.state.chat_agent = None
        logger.info("chat agent disabled (CHAT_ENABLED=false)")
    logger.info(
        "voice settings enabled=%s base_url=%s stt=%s tts_voice=%s",
        voice_settings.enabled,
        voice_settings.base_url,
        voice_settings.stt_model,
        voice_settings.tts_voice,
    )
    logger.info(
        "notify mode=%s google_ready=%s outlook_ready=%s email_provider=%s",
        notify_settings.mode,
        notify_settings.google_ready,
        notify_settings.outlook_ready,
        notify_settings.email_provider,
    )

    yield

    shutdown_chat_agent(getattr(app.state, "chat_agent", None))
    app.state.chat_agent = None
    close_adapter_http()
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


def _parse_calendar_day(raw: str | None, *, label: str) -> date | None:
    """Parse YYYY-MM-DD or return None when raw is empty."""
    if raw is None or not raw.strip():
        return None
    try:
        return date.fromisoformat(raw.strip())
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"{label} must be YYYY-MM-DD",
        ) from exc


def _calendar_window(
    *,
    from_day: date | None,
    to_day: date | None,
) -> tuple[date, date]:
    """Resolve inclusive busy-calendar bounds (default: clinic today + 6 days)."""
    start = from_day or clinic_today()
    end = to_day or (start + timedelta(days=_CALENDAR_DAYS - 1))
    if end < start:
        raise HTTPException(status_code=400, detail="to must be on or after from")
    if (end - start).days > _MAX_BUSY_RANGE_DAYS - 1:
        raise HTTPException(
            status_code=400,
            detail=f"range must be at most {_MAX_BUSY_RANGE_DAYS} days",
        )
    return start, end


def _busy_days_for_template(
    blocks: list[BusyBlock], start: date, end: date
) -> list[dict]:
    """Group busy blocks by clinic day for the doctor calendar UI."""
    by_day: dict[date, list[BusyBlock]] = defaultdict(list)
    for block in blocks:
        by_day[to_clinic(block.starts_at).date()].append(block)
    days: list[dict] = []
    cursor = start
    while cursor <= end:
        day_blocks = by_day.get(cursor, [])
        open_day = is_weekday(cursor)
        days.append(
            {
                "day": cursor,
                "label": cursor.strftime("%a %d %b"),
                "is_open": open_day,
                "blocks": [
                    {
                        "starts_at": to_clinic(b.starts_at),
                        "ends_at": to_clinic(b.ends_at),
                    }
                    for b in day_blocks
                ],
            }
        )
        cursor += timedelta(days=1)
    return days


def _load_busy_blocks(slug: str, start: date, end: date) -> list[BusyBlock]:
    """Load busy blocks for a professional when the DB is configured."""
    engine = get_engine()
    if engine is None:
        return []
    with Session(engine) as session:
        try:
            return list_busy_blocks_range(session, slug, start, end)
        except BookingError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc


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
def doctor_page(
    request: Request,
    slug: str,
    from_day: str | None = Query(default=None, alias="from"),
) -> HTMLResponse:
    """Render a doctor intro and busy-only calendar for that professional."""
    doctor = DOCTORS_BY_SLUG.get(slug)
    if doctor is None:
        raise HTTPException(status_code=404, detail="Doctor not found")
    start, end = _calendar_window(
        from_day=_parse_calendar_day(from_day, label="from"),
        to_day=None,
    )
    blocks = _load_busy_blocks(slug, start, end)
    prev_from = (start - timedelta(days=_CALENDAR_DAYS)).isoformat()
    next_from = (start + timedelta(days=_CALENDAR_DAYS)).isoformat()
    ctx = _page_context(
        request,
        title=doctor.name,
        user=optional_user(request),
        doctor=doctor,
    )
    ctx.update(
        {
            "calendar_from": start,
            "calendar_to": end,
            "calendar_days": _busy_days_for_template(blocks, start, end),
            "calendar_prev_from": prev_from,
            "calendar_next_from": next_from,
        }
    )
    return templates.TemplateResponse(request, "doctor.html", ctx)


@app.get("/api/doctors/{slug}/busy")
def api_doctor_busy(
    slug: str,
    db: Session = Depends(get_db),
    from_day: str | None = Query(default=None, alias="from"),
    to_day: str | None = Query(default=None, alias="to"),
) -> dict:
    """Return busy blocks for one professional (times only; no patient fields)."""
    if slug not in DOCTORS_BY_SLUG:
        raise HTTPException(status_code=404, detail="Doctor not found")
    start, end = _calendar_window(
        from_day=_parse_calendar_day(from_day, label="from"),
        to_day=_parse_calendar_day(to_day, label="to"),
    )
    try:
        blocks = list_busy_blocks_range(db, slug, start, end)
    except BookingError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "professional_slug": slug,
        "timezone": TIMEZONE_NAME,
        "from": start.isoformat(),
        "to": end.isoformat(),
        "busy": [busy_block_public_dict(block) for block in blocks],
    }


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


class SpeakBody(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


def _voice_settings(request: Request) -> VoiceSettings:
    """Return voice settings from app state or environment."""
    cached = getattr(request.app.state, "voice_settings", None)
    if isinstance(cached, VoiceSettings):
        return cached
    return VoiceSettings.from_env()


def _user_display_text(message: str) -> str:
    """Map FAQ chip tokens to friendly labels for the session transcript."""
    return faq_label(message) or message.strip()


def _session_display(request: Request) -> list[dict[str, str]]:
    """Return the UI chat transcript stored on the session cookie."""
    raw = request.session.get(SESSION_CHAT_DISPLAY_KEY)
    if not isinstance(raw, list):
        return []
    out: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        text = item.get("text")
        if role in {"user", "bot"} and isinstance(text, str) and text.strip():
            out.append({"role": role, "text": text})
    return out


def _append_display(request: Request, role: str, text: str) -> None:
    """Append one bubble to the session transcript (capped)."""
    rows = _session_display(request)
    rows.append({"role": role, "text": text.strip()})
    request.session[SESSION_CHAT_DISPLAY_KEY] = rows[-80:]


def _new_thread_id() -> str:
    """Allocate a messenger thread id for the session cookie."""
    return str(uuid.uuid4())


def _reset_chat_session(request: Request) -> str:
    """Clear messenger transcript, draft, and chat contact for this browser session."""
    request.session.pop(SESSION_CHAT_DISPLAY_KEY, None)
    request.session.pop(SESSION_THREAD_KEY, None)
    clear_chat_booking_state(request.session)
    thread_id = _new_thread_id()
    request.session[SESSION_THREAD_KEY] = thread_id
    return thread_id


@app.get("/api/chat/history")
def api_chat_history(
    request: Request,
    user: User | None = Depends(current_user),
) -> dict:
    """Return session-stored messenger bubbles for this browser session."""
    thread_id = request.session.get(SESSION_THREAD_KEY)
    if not isinstance(thread_id, str) or not thread_id:
        thread_id = _new_thread_id()
        request.session[SESSION_THREAD_KEY] = thread_id
    can_book = user is not None or has_visitor_contact_in_session(request.session)
    return {
        "thread_id": thread_id,
        "messages": _session_display(request),
        "can_book_now": can_book,
    }


@app.post("/api/chat/reset")
def api_chat_reset(request: Request) -> dict:
    """Clear the messenger transcript and start a new thread. Keeps login if any."""
    thread_id = _reset_chat_session(request)
    return {"ok": True, "thread_id": thread_id, "messages": []}


@app.post("/api/chat")
def api_chat(
    request: Request,
    body: ChatBody,
    db: Session = Depends(get_db),
    user: User | None = Depends(current_user),
    agent: ClinicAgent = Depends(get_chat_agent),
) -> dict:
    """Run one chat turn through ClinicAgent."""
    thread_id = request.session.get(SESSION_THREAD_KEY)
    if not isinstance(thread_id, str) or not thread_id:
        thread_id = _new_thread_id()
        request.session[SESSION_THREAD_KEY] = thread_id

    _append_display(request, "user", _user_display_text(body.message))
    try:
        result = agent.run_one_turn(
            message=body.message,
            session=request.session,
            db=db,
            thread_id=thread_id,
            user=user,
        )
    except Exception as exc:
        logger.exception("chat turn failed thread_id=%s", thread_id)
        reply = _CHAT_UNAVAILABLE
        _append_display(request, "bot", reply)
        raise HTTPException(status_code=502, detail=_CHAT_UNAVAILABLE) from exc

    _append_display(request, "bot", result.reply)
    return {
        "reply": result.reply,
        "thread_id": result.thread_id,
        "can_book_now": result.can_book_now,
    }


@app.post("/api/voice/transcribe")
async def api_voice_transcribe(
    request: Request,
    file: UploadFile = File(...),
) -> dict[str, str]:
    """Transcribe uploaded audio to text (does not call the chat agent)."""
    settings = _voice_settings(request)
    if not settings.enabled:
        raise HTTPException(status_code=503, detail="Voice is disabled.")

    audio = await file.read()
    if not audio:
        raise HTTPException(status_code=400, detail="Audio file is empty.")
    if len(audio) > _MAX_VOICE_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Audio file is too large.")

    filename = file.filename or "audio.webm"
    content_type = file.content_type or "application/octet-stream"
    try:
        text = transcribe_audio(
            audio,
            filename=filename,
            content_type=content_type,
            settings=settings,
        )
    except VoiceError as exc:
        raise HTTPException(status_code=502, detail=_VOICE_UNAVAILABLE) from exc

    if not text:
        raise HTTPException(
            status_code=400,
            detail="Could not hear speech. Try again or type your message.",
        )
    return {"text": text}


@app.post("/api/voice/speak")
def api_voice_speak(request: Request, body: SpeakBody) -> Response:
    """Synthesize Piper WAV for reply text (does not call the chat agent)."""
    settings = _voice_settings(request)
    if not settings.enabled:
        raise HTTPException(status_code=503, detail="Voice is disabled.")

    try:
        audio = synthesize_speech(body.text, settings=settings)
    except VoiceError as exc:
        raise HTTPException(status_code=502, detail=_VOICE_UNAVAILABLE) from exc

    return Response(content=audio, media_type="audio/wav")


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
    return booking_created_dict(booking)


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
    return booking_cancelled_dict(booking)


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
    return booking_rescheduled_dict(booking)


def run() -> None:
    """Start the HTTP server (used by the package console script)."""
    import uvicorn

    uvicorn.run(
        "clinic_ai_booking.main:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
    )
