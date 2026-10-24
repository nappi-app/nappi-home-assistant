"""Small client for the public nappi API (https://api.nappi.app/v1).

Only the public, documented API is used. The OpenAPI document lives at
https://api.nappi.app/v1/openapi.json.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
import uuid

import aiohttp

from .const import API_BASE_URL, USER_AGENT

REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=20)
DEFAULT_RETRY_AFTER = 60


class NappiError(Exception):
    """Base error for the nappi API."""

    def __init__(self, message: str = "", code: str | None = None, status: int | None = None) -> None:
        super().__init__(message or code or "nappi API error")
        self.code = code
        self.status = status


class NappiConnectionError(NappiError):
    """The API could not be reached, or answered with a server error."""


class NappiAuthError(NappiError):
    """The connection key is missing, invalid, revoked or expired (401), or its owner
    is no longer a caregiver in the household (403 forbidden). A new key is needed."""


class NappiPremiumRequiredError(NappiError):
    """The household no longer has nappi Premium (403 premium_required)."""


class NappiRateLimitedError(NappiError):
    """Too many requests (429). ``retry_after`` is in seconds."""

    def __init__(self, retry_after: int, message: str = "") -> None:
        super().__init__(message or "rate limited", "rate_limited", 429)
        self.retry_after = retry_after


class NappiBabyNotFoundError(NappiError):
    """The baby is not in the key's household any more (404 baby_not_found)."""


class NappiTimerNotRunningError(NappiError):
    """Stop was called but no timer of that type is running (409)."""


@dataclass(slots=True)
class Baby:
    """A baby in the key's household."""

    id: str
    name: str
    birth_date: str | None
    timezone: str | None


@dataclass(slots=True)
class StatusResult:
    """Running timers and latest entries. Both are None on 304 Not Modified,
    which the server only sends while no timer is running."""

    timers: list[dict[str, Any]] | None
    latest: dict[str, Any] | None
    etag: str | None

    @property
    def not_modified(self) -> bool:
        return self.latest is None


def _retry_after(headers: Any) -> int:
    value = headers.get("Retry-After") if headers else None
    try:
        seconds = int(float(value))
    except (TypeError, ValueError):
        return DEFAULT_RETRY_AFTER
    return max(1, seconds)


class NappiClient:
    """Async client bound to one connection key."""

    def __init__(self, session: aiohttp.ClientSession, api_key: str, base_url: str = API_BASE_URL) -> None:
        self._session = session
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, Any, Any]:
        """Send a request. Returns (status, parsed body or None, response headers)."""
        request_headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        }
        if headers:
            request_headers.update(headers)
        try:
            async with self._session.request(
                method,
                f"{self._base_url}{path}",
                json=json,
                headers=request_headers,
                timeout=REQUEST_TIMEOUT,
            ) as resp:
                status = resp.status
                body: Any = None
                if status != 304:
                    try:
                        body = await resp.json(content_type=None)
                    except (aiohttp.ContentTypeError, ValueError):
                        body = None
                resp_headers = resp.headers
        except (aiohttp.ClientError, TimeoutError) as err:
            raise NappiConnectionError(f"Could not reach nappi: {err}") from err

        if status < 400 or status == 304:
            return status, body, resp_headers

        error = body.get("error") if isinstance(body, dict) else None
        code = error.get("code") if isinstance(error, dict) else None
        message = (error.get("message") if isinstance(error, dict) else None) or f"HTTP {status}"

        if status == 401:
            raise NappiAuthError(message, code or "unauthorized", status)
        if status == 403:
            if code == "premium_required":
                raise NappiPremiumRequiredError(message, code, status)
            raise NappiAuthError(message, code or "forbidden", status)
        if status == 429:
            raise NappiRateLimitedError(_retry_after(resp_headers), message)
        if status == 404 and code == "baby_not_found":
            raise NappiBabyNotFoundError(message, code, status)
        if status == 409 and code == "timer_not_running":
            raise NappiTimerNotRunningError(message, code, status)
        if status >= 500:
            raise NappiConnectionError(message, code, status)
        raise NappiError(message, code, status)

    async def async_get_babies(self) -> list[Baby]:
        """List the babies in the key's household."""
        _, body, _ = await self._request("GET", "/babies")
        return [
            Baby(
                id=item["id"],
                name=item.get("name") or "",
                birth_date=item.get("birth_date"),
                timezone=item.get("timezone"),
            )
            for item in body or []
        ]

    async def async_get_status(self, baby_id: str, etag: str | None = None) -> StatusResult:
        """Read the running timers and the newest entry of each type in one request.

        Sends If-None-Match when an ETag is known.
        """
        headers = {"If-None-Match": etag} if etag else None
        status, body, resp_headers = await self._request("GET", f"/babies/{baby_id}/status", headers=headers)
        new_etag = resp_headers.get("ETag") if resp_headers else None
        if status == 304:
            return StatusResult(timers=None, latest=None, etag=new_etag or etag)
        body = body if isinstance(body, dict) else {}
        timers = body.get("timers")
        latest = body.get("latest")
        return StatusResult(
            timers=timers if isinstance(timers, list) else [],
            latest=latest if isinstance(latest, dict) else {},
            etag=new_etag,
        )

    async def async_log_entry(self, baby_id: str, entry: dict[str, Any]) -> dict[str, Any]:
        """Log a finished entry.

        One Idempotency-Key is used for the request and its single retry, so a
        connection drop after the server saved the entry never logs it twice.
        """
        headers = {"Idempotency-Key": f"ha-{uuid.uuid4()}"}
        path = f"/babies/{baby_id}/entries"
        try:
            _, body, _ = await self._request("POST", path, json=entry, headers=headers)
        except NappiConnectionError as err:
            if err.status is not None and err.status < 500:
                raise
            _, body, _ = await self._request("POST", path, json=entry, headers=headers)
        return body or {}

    async def async_start_timer(
        self, baby_id: str, timer_type: str, body: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Start a timer. Starting one that already runs is a no-op on the server."""
        _, result, _ = await self._request("POST", f"/babies/{baby_id}/timers/{timer_type}/start", json=body or {})
        return result or {}

    async def async_stop_timer(
        self, baby_id: str, timer_type: str, body: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Stop a timer and save its entry."""
        _, result, _ = await self._request("POST", f"/babies/{baby_id}/timers/{timer_type}/stop", json=body or {})
        return result or {}
