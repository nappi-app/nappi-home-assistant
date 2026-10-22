"""Setup, polling, and how API failures surface."""

from __future__ import annotations

from datetime import timedelta

from freezegun.api import FrozenDateTimeFactory

from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.const import STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.nappi.const import DOMAIN

from .conftest import KEY, SLEEP_TIMER, FakeNappi


async def _tick(hass: HomeAssistant, freezer: FrozenDateTimeFactory, seconds: float) -> None:
    freezer.tick(timedelta(seconds=seconds))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def test_setup_and_unload(hass: HomeAssistant, setup_entry: MockConfigEntry) -> None:
    assert setup_entry.state is ConfigEntryState.LOADED
    assert await hass.config_entries.async_unload(setup_entry.entry_id)
    assert setup_entry.state is ConfigEntryState.NOT_LOADED


async def test_polls_once_a_minute_with_two_reads(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, nappi: FakeNappi, setup_entry: MockConfigEntry
) -> None:
    assert len(nappi.calls) == 2
    await _tick(hass, freezer, 30)
    assert len(nappi.calls) == 2
    await _tick(hass, freezer, 31)
    assert [call[0] for call in nappi.calls] == ["latest", "timers", "latest", "timers"]
    assert all(call[3]["Authorization"] == f"Bearer {KEY}" for call in nappi.calls)


async def test_unchanged_entries_come_back_as_304(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, nappi: FakeNappi, setup_entry: MockConfigEntry
) -> None:
    await _tick(hass, freezer, 61)
    second = nappi.calls_to("latest")[1]
    assert second[3]["If-None-Match"] == nappi.etag
    # A 304 keeps what we had.
    assert hass.states.get("sensor.phoebe_last_diaper").attributes["diaper_type"] == "wet"

    nappi.latest["diaper"] = {"id": "d2", "time": "2026-10-21T19:00:00Z", "diaper_type": "mixed"}
    nappi.revision += 1
    await _tick(hass, freezer, 61)
    assert hass.states.get("sensor.phoebe_last_diaper").attributes["diaper_type"] == "mixed"


async def test_timer_started_elsewhere_shows_up_on_the_next_poll(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, nappi: FakeNappi, setup_entry: MockConfigEntry
) -> None:
    nappi.timers.append(dict(SLEEP_TIMER))
    await _tick(hass, freezer, 61)
    assert hass.states.get("switch.phoebe_sleep_timer").state == STATE_ON


async def test_expired_key_at_startup_asks_for_a_new_one(
    hass: HomeAssistant, nappi: FakeNappi, config_entry: MockConfigEntry
) -> None:
    nappi.fail("latest", 401, "token_expired")
    assert not await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert [flow["context"]["source"] for flow in flows] == [SOURCE_REAUTH]


async def test_revoked_key_while_running_asks_for_a_new_one(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, nappi: FakeNappi, setup_entry: MockConfigEntry
) -> None:
    nappi.fail("latest", 403, "forbidden")
    await _tick(hass, freezer, 61)
    flows = hass.config_entries.flow.async_progress()
    assert [flow["context"]["source"] for flow in flows] == [SOURCE_REAUTH]


async def test_premium_lapse_raises_a_repair_that_clears_itself(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, nappi: FakeNappi, setup_entry: MockConfigEntry
) -> None:
    issues = ir.async_get(hass)
    issue_id = f"premium_required_{setup_entry.entry_id}"

    nappi.fail("latest", 403, "premium_required")
    await _tick(hass, freezer, 61)
    issue = issues.async_get_issue(DOMAIN, issue_id)
    assert issue is not None
    assert issue.translation_placeholders == {"baby": "Phoebe"}
    assert hass.states.get("sensor.phoebe_last_diaper").state == STATE_UNAVAILABLE
    # Logging stays available: the button reports the problem if it's pressed.
    assert hass.states.get("button.phoebe_log_wet_diaper").state != STATE_UNAVAILABLE

    await _tick(hass, freezer, 61)
    assert issues.async_get_issue(DOMAIN, issue_id) is None
    assert hass.states.get("sensor.phoebe_last_diaper").state != STATE_UNAVAILABLE


async def test_premium_lapse_at_startup_keeps_retrying(
    hass: HomeAssistant, nappi: FakeNappi, config_entry: MockConfigEntry
) -> None:
    nappi.fail("latest", 403, "premium_required")
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.SETUP_RETRY
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"premium_required_{config_entry.entry_id}")


async def test_rate_limit_waits_for_retry_after(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, nappi: FakeNappi, setup_entry: MockConfigEntry
) -> None:
    nappi.fail("latest", 429, "rate_limited", {"Retry-After": "150"})
    await _tick(hass, freezer, 61)
    reads = len(nappi.calls)

    # The normal one-minute poll is skipped while the server asked us to wait.
    await _tick(hass, freezer, 61)
    await _tick(hass, freezer, 61)
    assert len(nappi.calls) == reads
    await _tick(hass, freezer, 30)
    assert len(nappi.calls) == reads + 2

    # Then back to once a minute.
    await _tick(hass, freezer, 61)
    assert len(nappi.calls) == reads + 4


async def test_removed_baby_raises_a_repair(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, nappi: FakeNappi, setup_entry: MockConfigEntry
) -> None:
    nappi.babies.clear()
    await _tick(hass, freezer, 61)
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"baby_not_found_{setup_entry.entry_id}")


async def test_removing_the_entry_clears_its_repairs(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, nappi: FakeNappi, setup_entry: MockConfigEntry
) -> None:
    nappi.fail("latest", 403, "premium_required")
    await _tick(hass, freezer, 61)
    await hass.config_entries.async_remove(setup_entry.entry_id)
    await hass.async_block_till_done()
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"premium_required_{setup_entry.entry_id}") is None
