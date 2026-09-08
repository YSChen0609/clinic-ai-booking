"""Build CalendarPort / EmailPort from environment (fakes or real adapters)."""

from __future__ import annotations

import logging

import httpx

from clinic_ai_booking.adapters.fanout import FanoutCalendar, FanoutEmail
from clinic_ai_booking.adapters.google import GoogleCalendar, GoogleEmail
from clinic_ai_booking.adapters.outlook import OutlookCalendar, OutlookEmail
from clinic_ai_booking.adapters.tokens import GoogleTokenSource, MicrosoftTokenSource
from clinic_ai_booking.config import NotifySettings
from clinic_ai_booking.fakes import FakeCalendar, FakeEmail
from clinic_ai_booking.notify import set_ports
from clinic_ai_booking.ports import CalendarPort, EmailPort

logger = logging.getLogger(__name__)

# Process-owned HTTP client for adapters (closed on reinstall/tests via reset).
_http: httpx.Client | None = None


def _http_client() -> httpx.Client:
    global _http
    if _http is None or _http.is_closed:
        _http = httpx.Client(timeout=30.0)
    return _http


def close_adapter_http() -> None:
    """Close the shared httpx client (app shutdown / tests)."""
    global _http
    if _http is not None and not _http.is_closed:
        _http.close()
    _http = None


def build_ports_from_env(
    settings: NotifySettings | None = None,
    *,
    http: httpx.Client | None = None,
) -> tuple[CalendarPort, EmailPort]:
    """Return calendar + email adapters for the current env."""
    cfg = settings or NotifySettings.from_env()
    if cfg.mode != "real":
        logger.info("notify adapters: fake (NOTIFY_MODE=%s)", cfg.mode)
        return FakeCalendar(), FakeEmail()

    client = http or _http_client()
    calendars: list[CalendarPort] = []
    emails: list[EmailPort] = []

    if cfg.google_ready:
        assert cfg.google_client_id and cfg.google_client_secret and cfg.google_refresh_token
        g_tokens = GoogleTokenSource(
            client_id=cfg.google_client_id,
            client_secret=cfg.google_client_secret,
            refresh_token=cfg.google_refresh_token,
            http=client,
        )
        calendars.append(
            GoogleCalendar(
                tokens=g_tokens,
                calendar_id=cfg.google_calendar_id,
                http=client,
            )
        )
        if cfg.email_provider in ("google", "both", "auto"):
            emails.append(
                GoogleEmail(
                    tokens=g_tokens,
                    sender=cfg.google_sender or "me",
                    http=client,
                )
            )

    if cfg.outlook_ready:
        assert cfg.ms_tenant_id and cfg.ms_client_id and cfg.ms_client_secret and cfg.ms_user_upn
        ms_tokens = MicrosoftTokenSource(
            tenant_id=cfg.ms_tenant_id,
            client_id=cfg.ms_client_id,
            client_secret=cfg.ms_client_secret,
            refresh_token=cfg.ms_refresh_token,
            http=client,
        )
        calendars.append(
            OutlookCalendar(
                tokens=ms_tokens,
                user_upn=cfg.ms_user_upn,
                calendar_id=cfg.ms_calendar_id,
                http=client,
            )
        )
        if cfg.email_provider in ("outlook", "both", "auto"):
            emails.append(
                OutlookEmail(
                    tokens=ms_tokens,
                    user_upn=cfg.ms_user_upn,
                    http=client,
                )
            )

    if not calendars and not emails:
        logger.warning(
            "NOTIFY_MODE=real but Google/Outlook credentials incomplete; using fakes"
        )
        return FakeCalendar(), FakeEmail()

    # Avoid double-emailing the patient when both providers are configured.
    if cfg.email_provider == "auto" and len(emails) > 1:
        # Prefer Outlook when both are ready (clinic M365 mailbox is common).
        emails = [e for e in emails if isinstance(e, OutlookEmail)] or emails[:1]

    calendar: CalendarPort
    if not calendars:
        calendar = FakeCalendar()
        logger.warning("notify: no calendar credentials; calendar writes use FakeCalendar")
    elif len(calendars) == 1:
        calendar = calendars[0]
    else:
        calendar = FanoutCalendar(calendars)

    email: EmailPort
    if not emails:
        email = FakeEmail()
        logger.warning("notify: no email credentials; mail uses FakeEmail")
    elif len(emails) == 1:
        email = emails[0]
    else:
        email = FanoutEmail(emails)

    logger.info(
        "notify adapters: calendar=%s email=%s",
        type(calendar).__name__,
        type(email).__name__,
    )
    return calendar, email


def install_ports_from_env(settings: NotifySettings | None = None) -> None:
    """Swap process ports from environment (app startup)."""
    calendar, email = build_ports_from_env(settings)
    set_ports(calendar, email)
