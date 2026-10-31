"""Buttons, timers, sensors and the bottle settings."""

from __future__ import annotations

import aiohttp
import pytest

from homeassistant.components.button import DOMAIN as BUTTON_DOMAIN, SERVICE_PRESS
from homeassistant.components.number import DOMAIN as NUMBER_DOMAIN, SERVICE_SET_VALUE
from homeassistant.components.select import DOMAIN as SELECT_DOMAIN, SERVICE_SELECT_OPTION
from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from homeassistant.config_entries import SOURCE_REAUTH
from homeassistant.const import (
    ATTR_ENTITY_ID,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_OFF,
    STATE_ON,
    STATE_UNKNOWN,
)
from homeassistant.core import HomeAssistant, State
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    mock_restore_cache_with_extra_data,
)

from custom_components.nappi.const import CONF_BOTTLE_UNIT, DOMAIN

from .conftest import BABY_ID, SLEEP_TIMER, FakeNappi


async def _press(hass: HomeAssistant, entity_id: str) -> None:
    await hass.services.async_call(BUTTON_DOMAIN, SERVICE_PRESS, {ATTR_ENTITY_ID: entity_id}, blocking=True)


async def test_sensors_show_the_latest_entries(hass: HomeAssistant, setup_entry: MockConfigEntry) -> None:
    diaper = hass.states.get("sensor.phoebe_last_diaper")
    assert diaper.state == "2026-10-21T18:30:00+00:00"
    assert diaper.attributes["diaper_type"] == "wet"
    assert diaper.attributes["entry_id"] == "d1"
    assert diaper.attributes["device_class"] == "timestamp"

    sleep = hass.states.get("sensor.phoebe_last_sleep")
    # A sleep's time is when it ended.
    assert sleep.state == "2026-10-21T13:30:00+00:00"
    assert sleep.attributes["start_time"] == "2026-10-21T12:00:00Z"
    assert sleep.attributes["duration_minutes"] == 90
    assert sleep.attributes["sleep_type"] == "nap"

    bottle = hass.states.get("sensor.phoebe_last_bottle")
    assert bottle.attributes["amount_ml"] == 120

    assert hass.states.get("sensor.phoebe_last_nursing").state == STATE_UNKNOWN


async def test_rarely_used_sensors_start_disabled(hass: HomeAssistant, setup_entry: MockConfigEntry) -> None:
    registry = er.async_get(hass)
    for key in ("potty", "solids", "activity", "growth", "medication"):
        entity_id = registry.async_get_entity_id("sensor", DOMAIN, f"{BABY_ID}_last_{key}")
        assert registry.async_get(entity_id).disabled_by is er.RegistryEntryDisabler.INTEGRATION
    assert hass.states.get("sensor.phoebe_last_growth_measurement") is None


async def test_growth_sensor_is_a_date(hass: HomeAssistant, nappi: FakeNappi, config_entry: MockConfigEntry) -> None:
    registry = er.async_get(hass)
    registry.async_get_or_create(
        "sensor", DOMAIN, f"{BABY_ID}_last_growth", config_entry=config_entry, disabled_by=None
    )
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    entity_id = registry.async_get_entity_id("sensor", DOMAIN, f"{BABY_ID}_last_growth")
    state = hass.states.get(entity_id)
    assert state.state == "2026-10-20"
    assert state.attributes["weight_kg"] == 6.2


@pytest.mark.parametrize(
    ("entity_id", "diaper_type"),
    [
        ("button.phoebe_log_wet_diaper", "wet"),
        ("button.phoebe_log_dirty_diaper", "poo"),
        ("button.phoebe_log_mixed_diaper", "mixed"),
    ],
)
async def test_diaper_buttons_log_and_refresh(
    hass: HomeAssistant, nappi: FakeNappi, setup_entry: MockConfigEntry, entity_id: str, diaper_type: str
) -> None:
    await _press(hass, entity_id)
    await hass.async_block_till_done()

    (call,) = nappi.calls_to("log")
    assert call[2] == {"type": "diaper", "diaper_type": diaper_type}
    assert call[3]["Idempotency-Key"].startswith("ha-")
    # The sensor reflects the new entry without waiting for the next poll.
    assert hass.states.get("sensor.phoebe_last_diaper").attributes["diaper_type"] == diaper_type


async def test_bottle_button_uses_the_amount_and_contents(
    hass: HomeAssistant, nappi: FakeNappi, setup_entry: MockConfigEntry
) -> None:
    await _press(hass, "button.phoebe_log_bottle")
    assert nappi.calls_to("log")[-1][2] == {"type": "bottle", "amount": 120, "unit": "ml"}

    await hass.services.async_call(
        NUMBER_DOMAIN, SERVICE_SET_VALUE, {ATTR_ENTITY_ID: "number.phoebe_bottle_amount", "value": 150}, blocking=True
    )
    await hass.services.async_call(
        SELECT_DOMAIN,
        SERVICE_SELECT_OPTION,
        {ATTR_ENTITY_ID: "select.phoebe_bottle_contents", "option": "breast_milk"},
        blocking=True,
    )
    await _press(hass, "button.phoebe_log_bottle")
    assert nappi.calls_to("log")[-1][2] == {
        "type": "bottle",
        "amount": 150,
        "unit": "ml",
        "bottle_type": "breast_milk",
    }


async def test_bottle_in_ounces(hass: HomeAssistant, nappi: FakeNappi, config_entry: MockConfigEntry) -> None:
    hass.config_entries.async_update_entry(config_entry, options={CONF_BOTTLE_UNIT: "oz"})
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    await _press(hass, "button.phoebe_log_bottle")
    assert nappi.calls_to("log")[-1][2] == {"type": "bottle", "amount": 4, "unit": "oz"}


async def test_bottle_settings_survive_a_restart(
    hass: HomeAssistant, nappi: FakeNappi, config_entry: MockConfigEntry
) -> None:
    mock_restore_cache_with_extra_data(
        hass,
        [
            (State("select.phoebe_bottle_contents", "formula"), {}),
            (
                State("number.phoebe_bottle_amount", "90"),
                {
                    "native_value": 90,
                    "native_min_value": 5,
                    "native_max_value": 500,
                    "native_step": 5,
                    "native_unit_of_measurement": "mL",
                },
            ),
        ],
    )
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get("number.phoebe_bottle_amount").state == "90"
    assert hass.states.get("select.phoebe_bottle_contents").state == "formula"

    await _press(hass, "button.phoebe_log_bottle")
    assert nappi.calls_to("log")[-1][2] == {"type": "bottle", "amount": 90, "unit": "ml", "bottle_type": "formula"}


async def test_an_amount_saved_in_ml_is_not_reused_as_ounces(
    hass: HomeAssistant, nappi: FakeNappi, config_entry: MockConfigEntry
) -> None:
    mock_restore_cache_with_extra_data(
        hass,
        [
            (
                State("number.phoebe_bottle_amount", "12"),
                {
                    "native_value": 12,
                    "native_min_value": 5,
                    "native_max_value": 500,
                    "native_step": 5,
                    "native_unit_of_measurement": "mL",
                },
            )
        ],
    )
    hass.config_entries.async_update_entry(config_entry, options={CONF_BOTTLE_UNIT: "oz"})
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get("number.phoebe_bottle_amount").state == "4"


PUMPING_TIMER = {
    "type": "pumping",
    "started_at": "2026-10-21T15:40:00Z",
    "paused": False,
    "elapsed_seconds": 900,
}


async def test_pumping_button_logs_the_amount_when_no_timer_runs(
    hass: HomeAssistant, nappi: FakeNappi, setup_entry: MockConfigEntry
) -> None:
    await _press(hass, "button.phoebe_log_pumping")
    assert nappi.calls_to("log")[-1][2] == {"type": "pumping", "amount": 120, "unit": "ml"}
    assert nappi.calls_to("log")[-1][3]["Idempotency-Key"].startswith("ha-")

    await hass.services.async_call(
        NUMBER_DOMAIN, SERVICE_SET_VALUE, {ATTR_ENTITY_ID: "number.phoebe_pumping_amount", "value": 90}, blocking=True
    )
    await _press(hass, "button.phoebe_log_pumping")
    assert nappi.calls_to("log")[-1][2] == {"type": "pumping", "amount": 90, "unit": "ml"}
    # The sensor reflects the new entry without waiting for the next poll.
    assert hass.states.get("sensor.phoebe_last_pumping").state != STATE_UNKNOWN


async def test_pumping_in_ounces(hass: HomeAssistant, nappi: FakeNappi, config_entry: MockConfigEntry) -> None:
    hass.config_entries.async_update_entry(config_entry, options={CONF_BOTTLE_UNIT: "oz"})
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    await _press(hass, "button.phoebe_log_pumping")
    assert nappi.calls_to("log")[-1][2] == {"type": "pumping", "amount": 4, "unit": "oz"}


async def test_pumping_button_stops_the_running_timer_with_the_amount(
    hass: HomeAssistant, nappi: FakeNappi, config_entry: MockConfigEntry
) -> None:
    nappi.timers.append(dict(PUMPING_TIMER))
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get("switch.phoebe_pumping_timer").state == STATE_ON

    await _press(hass, "button.phoebe_log_pumping")

    # One entry with the duration and the amount, not a second entry.
    (stop,) = nappi.calls_to("stop:pumping")
    assert stop[2] == {"amount": 120, "unit": "ml"}
    assert not nappi.calls_to("log")
    assert hass.states.get("switch.phoebe_pumping_timer").state == STATE_OFF


async def test_pumping_button_stops_a_timer_started_since_the_last_poll(
    hass: HomeAssistant, nappi: FakeNappi, setup_entry: MockConfigEntry
) -> None:
    assert hass.states.get("switch.phoebe_pumping_timer").state == STATE_OFF
    nappi.timers.append(dict(PUMPING_TIMER))  # Started on the phone since the last poll.

    await _press(hass, "button.phoebe_log_pumping")

    assert nappi.calls_to("stop:pumping")[-1][2] == {"amount": 120, "unit": "ml"}
    assert not nappi.calls_to("log")


async def test_pumping_button_logs_when_the_timer_was_stopped_elsewhere(
    hass: HomeAssistant, nappi: FakeNappi, config_entry: MockConfigEntry
) -> None:
    nappi.timers.append(dict(PUMPING_TIMER))
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    nappi.timers.clear()  # Stopped on the phone since the last poll.

    await _press(hass, "button.phoebe_log_pumping")

    assert nappi.calls_to("log")[-1][2] == {"type": "pumping", "amount": 120, "unit": "ml"}
    assert hass.states.get("switch.phoebe_pumping_timer").state == STATE_OFF


async def test_pumping_button_needs_an_amount(
    hass: HomeAssistant, nappi: FakeNappi, setup_entry: MockConfigEntry
) -> None:
    setup_entry.runtime_data.pumping_amount = None
    with pytest.raises(HomeAssistantError) as err:
        await _press(hass, "button.phoebe_log_pumping")
    assert err.value.translation_key == "no_pumping_amount"
    assert not nappi.calls_to("log")
    assert not nappi.calls_to("stop:pumping")


async def test_pumping_amount_survives_a_restart(
    hass: HomeAssistant, nappi: FakeNappi, config_entry: MockConfigEntry
) -> None:
    mock_restore_cache_with_extra_data(
        hass,
        [
            (
                State("number.phoebe_pumping_amount", "200"),
                {
                    "native_value": 200,
                    "native_min_value": 5,
                    "native_max_value": 500,
                    "native_step": 5,
                    "native_unit_of_measurement": "mL",
                },
            )
        ],
    )
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get("number.phoebe_pumping_amount").state == "200"
    assert hass.states.get("number.phoebe_bottle_amount").state == "120"

    await _press(hass, "button.phoebe_log_pumping")
    assert nappi.calls_to("log")[-1][2] == {"type": "pumping", "amount": 200, "unit": "ml"}


async def test_timer_switch_mirrors_and_controls_the_timer(
    hass: HomeAssistant, nappi: FakeNappi, config_entry: MockConfigEntry
) -> None:
    nappi.timers.append(dict(SLEEP_TIMER))
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    sleep = hass.states.get("switch.phoebe_sleep_timer")
    assert sleep.state == STATE_ON
    assert sleep.attributes["started_at"] == "2026-10-21T15:00:00Z"
    assert hass.states.get("switch.phoebe_nursing_timer").state == STATE_OFF

    await hass.services.async_call(
        SWITCH_DOMAIN, SERVICE_TURN_OFF, {ATTR_ENTITY_ID: "switch.phoebe_sleep_timer"}, blocking=True
    )
    await hass.async_block_till_done()
    assert hass.states.get("switch.phoebe_sleep_timer").state == STATE_OFF
    assert len(nappi.calls_to("stop:sleep")) == 1

    reads = len(nappi.calls_to("status"))
    await hass.services.async_call(
        SWITCH_DOMAIN, SERVICE_TURN_ON, {ATTR_ENTITY_ID: "switch.phoebe_nursing_timer"}, blocking=True
    )
    assert hass.states.get("switch.phoebe_nursing_timer").state == STATE_ON
    # Starting uses the server's answer instead of spending a read.
    assert len(nappi.calls_to("status")) == reads


async def test_stopping_a_timer_someone_else_stopped(
    hass: HomeAssistant, nappi: FakeNappi, config_entry: MockConfigEntry
) -> None:
    nappi.timers.append(dict(SLEEP_TIMER))
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    nappi.timers.clear()  # Stopped on the phone since the last poll.

    await hass.services.async_call(
        SWITCH_DOMAIN, SERVICE_TURN_OFF, {ATTR_ENTITY_ID: "switch.phoebe_sleep_timer"}, blocking=True
    )
    assert hass.states.get("switch.phoebe_sleep_timer").state == STATE_OFF


async def test_rate_limited_write_says_when_to_retry(
    hass: HomeAssistant, nappi: FakeNappi, setup_entry: MockConfigEntry
) -> None:
    nappi.fail("log", 429, "rate_limited", {"Retry-After": "42"})
    with pytest.raises(HomeAssistantError) as err:
        await _press(hass, "button.phoebe_log_wet_diaper")
    assert err.value.translation_key == "rate_limited"
    assert err.value.translation_placeholders == {"seconds": "42"}


async def test_premium_lapsed_write(hass: HomeAssistant, nappi: FakeNappi, setup_entry: MockConfigEntry) -> None:
    nappi.fail("start:sleep", 403, "premium_required")
    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call(
            SWITCH_DOMAIN, SERVICE_TURN_ON, {ATTR_ENTITY_ID: "switch.phoebe_sleep_timer"}, blocking=True
        )
    assert err.value.translation_key == "premium_required"


async def test_revoked_key_on_write_starts_reauth(
    hass: HomeAssistant, nappi: FakeNappi, setup_entry: MockConfigEntry
) -> None:
    nappi.fail("log", 401, "unauthorized")
    with pytest.raises(HomeAssistantError) as err:
        await _press(hass, "button.phoebe_log_wet_diaper")
    assert err.value.translation_key == "auth_failed"
    await hass.async_block_till_done()
    flows = hass.config_entries.flow.async_progress()
    assert [flow["context"]["source"] for flow in flows] == [SOURCE_REAUTH]


async def test_a_dropped_connection_retries_with_the_same_idempotency_key(
    hass: HomeAssistant, nappi: FakeNappi, setup_entry: MockConfigEntry
) -> None:
    nappi.raise_once("log", aiohttp.ServerDisconnectedError())
    await _press(hass, "button.phoebe_log_wet_diaper")
    first, second = nappi.calls_to("log")
    assert first[3]["Idempotency-Key"] == second[3]["Idempotency-Key"]


async def test_each_press_gets_its_own_idempotency_key(
    hass: HomeAssistant, nappi: FakeNappi, setup_entry: MockConfigEntry
) -> None:
    await _press(hass, "button.phoebe_log_wet_diaper")
    await _press(hass, "button.phoebe_log_wet_diaper")
    first, second = nappi.calls_to("log")
    assert first[3]["Idempotency-Key"] != second[3]["Idempotency-Key"]
