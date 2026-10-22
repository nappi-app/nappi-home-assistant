"""Fixtures: a fake nappi API behind Home Assistant's aiohttp test mocker."""

from __future__ import annotations

from collections.abc import Generator
import re
from typing import Any

import pytest
from yarl import URL

from homeassistant.const import CONF_API_KEY
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
    AiohttpClientMockResponse,
)

from custom_components.nappi.const import API_BASE_URL, CONF_BABY_ID, CONF_BABY_NAME, DOMAIN

KEY = "nappi_" + "ab" * 24
NEW_KEY = "nappi_" + "cd" * 24
BABY_ID = "11111111-1111-4111-8111-111111111111"
TWIN_ID = "22222222-2222-4222-8222-222222222222"

LATEST = {
    "sleep": {
        "id": "s1",
        "start_time": "2026-10-21T12:00:00Z",
        "end_time": "2026-10-21T13:30:00Z",
        "duration_minutes": 90,
        "sleep_type": "nap",
    },
    "nursing": None,
    "bottle": {"id": "b1", "time": "2026-10-21T14:00:00Z", "amount_ml": 120, "bottle_type": "formula"},
    "diaper": {"id": "d1", "time": "2026-10-21T14:30:00-04:00", "diaper_type": "wet"},
    "potty": None,
    "pumping": None,
    "solids": None,
    "activity": None,
    "growth": {
        "id": "g1",
        "date": "2026-10-20",
        "weight_kg": 6.2,
        "height_cm": 61.5,
        "head_circumference_cm": None,
    },
    "medication": None,
}

SLEEP_TIMER = {
    "type": "sleep",
    "started_at": "2026-10-21T15:00:00Z",
    "paused": False,
    "elapsed_seconds": 600,
}


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> Generator[None]:
    """Load custom_components/nappi in every test."""
    yield


class FakeNappi:
    """A stateful stand-in for api.nappi.app.

    Queue a failure for a route with ``fail(route, status, code)``; it is
    returned once, then the route behaves normally again.
    """

    def __init__(self, mocker: AiohttpClientMocker) -> None:
        self.mocker = mocker
        self.babies = [{"id": BABY_ID, "name": "Phoebe", "birth_date": "2026-06-01", "timezone": "America/New_York"}]
        self.latest: dict[str, Any] = {k: (dict(v) if v else None) for k, v in LATEST.items()}
        self.timers: list[dict[str, Any]] = []
        self.revision = 1
        self.failures: dict[str, list[tuple[int, str | None, dict[str, str]]]] = {}
        self.exceptions: dict[str, list[Exception]] = {}
        self.calls: list[tuple[str, str, Any, dict[str, str]]] = []
        pattern = re.compile(re.escape(API_BASE_URL) + r"/.*")
        mocker.get(pattern, side_effect=self._handle)
        mocker.post(pattern, side_effect=self._handle)

    @property
    def etag(self) -> str:
        return f'W/"r{self.revision}"'

    def fail(self, route: str, status: int, code: str | None = None, headers: dict[str, str] | None = None) -> None:
        self.failures.setdefault(route, []).append((status, code, headers or {}))

    def raise_once(self, route: str, exc: Exception) -> None:
        self.exceptions.setdefault(route, []).append(exc)

    def calls_to(self, route: str) -> list[tuple[str, str, Any, dict[str, str]]]:
        return [call for call in self.calls if call[0] == route]

    @staticmethod
    def _route(method: str, path: str) -> str:
        parts = path.removeprefix("/v1/").split("/")
        if parts == ["babies"]:
            return "babies"
        if parts[2:] == ["entries", "latest"]:
            return "latest"
        if parts[2:] == ["timers"]:
            return "timers"
        if parts[2:] == ["entries"]:
            return "log"
        if len(parts) == 5 and parts[2] == "timers":
            return f"{parts[4]}:{parts[3]}"
        raise AssertionError(f"unexpected {method} {path}")

    def _respond(self, method: str, url: URL, status: int, body: Any = None, headers: dict[str, str] | None = None):
        return AiohttpClientMockResponse(method, url, status=status, json=body, headers=headers)

    async def _handle(self, method: str, url: URL, data: Any) -> AiohttpClientMockResponse:
        headers = dict(self.mocker.mock_calls[-1][3] or {})
        route = self._route(method.upper(), url.path)
        self.calls.append((route, method.upper(), data, headers))

        if self.exceptions.get(route):
            raise self.exceptions[route].pop(0)
        if self.failures.get(route):
            status, code, extra = self.failures[route].pop(0)
            return self._respond(method, url, status, {"error": {"code": code, "message": code or ""}}, extra)

        baby_id = url.path.split("/")[3] if route != "babies" else None
        if baby_id is not None and baby_id not in {b["id"] for b in self.babies}:
            return self._respond(method, url, 404, {"error": {"code": "baby_not_found", "message": "gone"}})

        if route == "babies":
            return self._respond(method, url, 200, self.babies)
        if route == "latest":
            if headers.get("If-None-Match") == self.etag:
                return self._respond(method, url, 304, headers={"ETag": self.etag})
            return self._respond(method, url, 200, self.latest, {"ETag": self.etag})
        if route == "timers":
            return self._respond(method, url, 200, self.timers)
        if route == "log":
            self.revision += 1
            entry = {"id": f"e{self.revision}", "time": "2026-10-21T16:00:00Z"}
            if data["type"] == "diaper":
                entry["diaper_type"] = data["diaper_type"]
            self.latest[data["type"]] = entry
            return self._respond(method, url, 201, {"id": entry["id"], "type": data["type"], "duplicate": False})

        action, timer_type = route.split(":")
        running = next((t for t in self.timers if t["type"] == timer_type), None)
        if action == "start":
            if running:
                return self._respond(method, url, 200, {"status": "already_running", "timer": running})
            timer = {"type": timer_type, "started_at": "2026-10-21T16:00:00Z", "paused": False, "elapsed_seconds": 0}
            self.timers.append(timer)
            return self._respond(method, url, 201, {"status": "started", "timer": timer})
        if running is None:
            return self._respond(method, url, 409, {"error": {"code": "timer_not_running", "message": "no"}})
        self.timers.remove(running)
        self.revision += 1
        return self._respond(
            method,
            url,
            200,
            {
                "status": "stopped",
                "entry": {
                    "id": "t1",
                    "type": timer_type,
                    "start_time": running["started_at"],
                    "end_time": "2026-10-21T16:10:00Z",
                    "duration_minutes": 10,
                },
            },
        )


@pytest.fixture
def nappi(aioclient_mock: AiohttpClientMocker) -> FakeNappi:
    return FakeNappi(aioclient_mock)


@pytest.fixture
def config_entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Phoebe",
        unique_id=BABY_ID,
        data={CONF_API_KEY: KEY, CONF_BABY_ID: BABY_ID, CONF_BABY_NAME: "Phoebe"},
    )
    entry.add_to_hass(hass)
    return entry


@pytest.fixture
async def setup_entry(hass: HomeAssistant, nappi: FakeNappi, config_entry: MockConfigEntry) -> MockConfigEntry:
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    return config_entry
