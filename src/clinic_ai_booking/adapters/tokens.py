"""OAuth token helpers for Google and Microsoft (httpx only)."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

import httpx

logger = logging.getLogger(__name__)

GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
MS_TOKEN_URL_TMPL = "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"


class TokenError(Exception):
    """OAuth token request failed."""


@dataclass
class _CachedToken:
    access_token: str
    expires_at: float


@dataclass
class GoogleTokenSource:
    """Refresh-token grant for a clinic Google account."""

    client_id: str
    client_secret: str
    refresh_token: str
    http: httpx.Client
    _cache: _CachedToken | None = field(default=None, init=False, repr=False)

    def access_token(self) -> str:
        """Return a valid Google access token."""
        now = time.monotonic()
        if self._cache is not None and self._cache.expires_at > now + 60:
            return self._cache.access_token
        response = self.http.post(
            GOOGLE_TOKEN_URL,
            data={
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "refresh_token": self.refresh_token,
                "grant_type": "refresh_token",
            },
        )
        if response.status_code >= 400:
            logger.error("Google token error status=%s body=%s", response.status_code, response.text[:300])
            raise TokenError("Google OAuth token refresh failed")
        payload = response.json()
        token = payload.get("access_token")
        if not isinstance(token, str) or not token:
            raise TokenError("Google OAuth response missing access_token")
        expires_in = int(payload.get("expires_in") or 3600)
        self._cache = _CachedToken(token, now + expires_in)
        return token


@dataclass
class MicrosoftTokenSource:
    """Client-credentials or refresh-token grant for Microsoft Graph."""

    tenant_id: str
    client_id: str
    client_secret: str
    http: httpx.Client
    refresh_token: str | None = None
    _cache: _CachedToken | None = field(default=None, init=False, repr=False)

    def access_token(self) -> str:
        """Return a valid Microsoft Graph access token."""
        now = time.monotonic()
        if self._cache is not None and self._cache.expires_at > now + 60:
            return self._cache.access_token
        url = MS_TOKEN_URL_TMPL.format(tenant=self.tenant_id)
        if self.refresh_token:
            data = {
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "refresh_token": self.refresh_token,
                "grant_type": "refresh_token",
                "scope": "https://graph.microsoft.com/.default",
            }
        else:
            data = {
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "grant_type": "client_credentials",
                "scope": "https://graph.microsoft.com/.default",
            }
        response = self.http.post(url, data=data)
        if response.status_code >= 400:
            logger.error(
                "Microsoft token error status=%s body=%s",
                response.status_code,
                response.text[:300],
            )
            raise TokenError("Microsoft OAuth token request failed")
        payload = response.json()
        token = payload.get("access_token")
        if not isinstance(token, str) or not token:
            raise TokenError("Microsoft OAuth response missing access_token")
        expires_in = int(payload.get("expires_in") or 3600)
        self._cache = _CachedToken(token, now + expires_in)
        return token
