"""Real CalendarPort / EmailPort adapters (Google + Outlook)."""

from clinic_ai_booking.adapters.wiring import build_ports_from_env, install_ports_from_env

__all__ = ["build_ports_from_env", "install_ports_from_env"]
