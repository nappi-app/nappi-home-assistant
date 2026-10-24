"""Setup, polling, and how API failures surface."""

from __future__ import annotations

from datetime import timedelta

from freezegun.api import FrozenDateTimeFactory

from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.const import ATTR_ENTITY_ID, SERVICE_TURN_ON, STATE_OFF, STATE_ON, STATE_UNAVAILABLE
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


async def test_polls_every_two_minutes_with_one_read(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, nappi: FakeNappi, setup_entry: MockConfigEntry
) -> None:
    assert len(nappi.calls) == 1
    await _tick(hass, freezer, 61)
    assert len(nappi.calls) == 1
    await _tick(hass, freezer, 60)
    assert [call[0] for call in nappi.calls] == ["status", "status"]
    assert all(call[3]["Authorization"] == f"Bearer {KEY}" for call in nappi.calls)


async def test_unchanged_status_comes_back_as_304(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, nappi: FakeNappi, setup_entry: MockConfigEntry
) -> None:
    await _tick(hass, freezer, 121)
    second = nappi.calls_to("status")[1]
    assert second[3]["If-None-Match"] == nappi.etag
    # A 304 keeps what we had.
    assert hass.states.get("sensor.phoebe_last_diaper").attributes["diaper_type"] == "wet"

    nappi.latest["diaper"] = {"id": "d2", "time": "2026-10-21T19:00:00Z", "diaper_type": "mixed"}
    nappi.revision += 1
    await _tick(hass, freezer, 121)
    assert hass.states.get("sensor.phoebe_last_diaper").attributes["diaper_type"] == "mixed"


async def test_timer_discarded_elsewhere_after_starting_it_here(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, nappi: FakeNappi, setup_entry: MockConfigEntry
) -> None:
    await hass.services.async_call(
        SWITCH_DOMAIN, SERVICE_TURN_ON, {ATTR_ENTITY_ID: "switch.phoebe_nursing_timer"}, blocking=True
    )
    assert hass.states.get("switch.phoebe_nursing_timer").state == STATE_ON
    # Discarded on the phone: no entry is saved, so the revision stays the
    # same and the server state matches the ETag from before the start.
    nappi.timers.clear()
    await _tick(hass, freezer, 121)
    assert hass.states.get("switch.phoebe_nursing_timer").state == STATE_OFF


async def test_timer_started_elsewhere_shows_up_on_the_next_poll(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, nappi: FakeNappi, setup_entry: MockConfigEntry
) -> None:
    nappi.timers.append(dict(SLEEP_TIMER))
    await _tick(hass, freezer, 121)
    assert hass.states.get("switch.phoebe_sleep_timer").state == STATE_ON


async def test_expired_key_at_startup_asks_for_a_new_one(
    hass: HomeAssistant, nappi: FakeNappi, config_entry: MockConfigEntry
) -> None:
    nappi.fail("status", 401, "token_expired")
    assert not await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert [flow["context"]["source"] for flow in flows] == [SOURCE_REAUTH]


async def test_revoked_key_while_running_asks_for_a_new_one(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, nappi: FakeNappi, setup_entry: MockConfigEntry
) -> None:
    nappi.fail("status", 403, "forbidden")
    await _tick(hass, freezer, 121)
    flows = hass.config_entries.flow.async_progress()
    assert [flow["context"]["source"] for flow in flows] == [SOURCE_REAUTH]


async def test_premium_lapse_raises_a_repair_that_clears_itself(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, nappi: FakeNappi, setup_entry: MockConfigEntry
) -> None:
    issues = ir.async_get(hass)
    issue_id = f"premium_required_{setup_entry.entry_id}"

    nappi.fail("status", 403, "premium_required")
    await _tick(hass, freezer, 121)
    issue = issues.async_get_issue(DOMAIN, issue_id)
    assert issue is not None
    assert issue.translation_placeholders == {"baby": "Phoebe"}
    assert hass.states.get("sensor.phoebe_last_diaper").state == STATE_UNAVAILABLE
    # Logging stays available: the button reports the problem if it's pressed.
    assert hass.states.get("button.phoebe_log_wet_diaper").state != STATE_UNAVAILABLE

    await _tick(hass, freezer, 121)
    assert issues.async_get_issue(DOMAIN, issue_id) is None
    assert hass.states.get("sensor.phoebe_last_diaper").state != STATE_UNAVAILABLE


async def test_premium_lapse_at_startup_keeps_retrying(
    hass: HomeAssistant, nappi: FakeNappi, config_entry: MockConfigEntry
) -> None:
    nappi.fail("status", 403, "premium_required")
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.SETUP_RETRY
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"premium_required_{config_entry.entry_id}")


async def test_rate_limit_waits_for_retry_after(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, nappi: FakeNappi, setup_entry: MockConfigEntry
) -> None:
    nappi.fail("status", 429, "rate_limited", {"Retry-After": "150"})
    await _tick(hass, freezer, 121)
    reads = len(nappi.calls)

    # The normal two-minute poll is skipped while the server asked us to wait.
    await _tick(hass, freezer, 121)
    assert len(nappi.calls) == reads
    await _tick(hass, freezer, 30)
    assert len(nappi.calls) == reads + 1

    # Then back to every two minutes.
    await _tick(hass, freezer, 121)
    assert len(nappi.calls) == reads + 2


async def test_removed_baby_raises_a_repair(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, nappi: FakeNappi, setup_entry: MockConfigEntry
) -> None:
    nappi.babies.clear()
    await _tick(hass, freezer, 121)
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"baby_not_found_{setup_entry.entry_id}")


async def test_removing_the_entry_clears_its_repairs(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, nappi: FakeNappi, setup_entry: MockConfigEntry
) -> None:
    nappi.fail("status", 403, "premium_required")
    await _tick(hass, freezer, 121)
    await hass.config_entries.async_remove(setup_entry.entry_id)
    await hass.async_block_till_done()
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"premium_required_{setup_entry.entry_id}") is None
