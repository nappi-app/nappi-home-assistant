"""Config, reauth and options flows."""

from __future__ import annotations

import aiohttp
import pytest

from homeassistant import config_entries
from homeassistant.const import CONF_API_KEY
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.nappi.const import CONF_BABY_ID, CONF_BABY_NAME, CONF_BOTTLE_UNIT, DOMAIN

from .conftest import BABY_ID, KEY, NEW_KEY, TWIN_ID, FakeNappi


async def _start(hass: HomeAssistant):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    return result


async def test_one_baby_creates_the_entry_straight_away(hass: HomeAssistant, nappi: FakeNappi) -> None:
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_API_KEY: f"  {KEY} "})

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Phoebe"
    assert result["data"] == {CONF_API_KEY: KEY, CONF_BABY_ID: BABY_ID, CONF_BABY_NAME: "Phoebe"}
    assert result["result"].unique_id == BABY_ID
    # The key travels as a bearer token.
    assert nappi.calls_to("babies")[0][3]["Authorization"] == f"Bearer {KEY}"


async def test_bearer_prefix_is_accepted(hass: HomeAssistant, nappi: FakeNappi) -> None:
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_API_KEY: f"Bearer {KEY}"})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_API_KEY] == KEY


async def test_twins_pick_a_baby(hass: HomeAssistant, nappi: FakeNappi) -> None:
    nappi.babies.append({"id": TWIN_ID, "name": "Ada", "birth_date": "2026-06-01", "timezone": None})
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_API_KEY: KEY})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "baby"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_BABY_ID: TWIN_ID})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Ada"
    assert result["data"][CONF_BABY_ID] == TWIN_ID


async def test_babies_already_set_up_are_not_offered(
    hass: HomeAssistant, nappi: FakeNappi, config_entry: MockConfigEntry
) -> None:
    nappi.babies.append({"id": TWIN_ID, "name": "Ada", "birth_date": None, "timezone": None})
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_API_KEY: KEY})
    # Phoebe is configured, so the only one left is created without asking.
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Ada"


async def test_all_babies_configured_aborts(
    hass: HomeAssistant, nappi: FakeNappi, config_entry: MockConfigEntry
) -> None:
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_API_KEY: KEY})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


@pytest.mark.parametrize(
    ("status", "code", "error"),
    [
        (401, "unauthorized", "invalid_auth"),
        (401, "token_expired", "invalid_auth"),
        (403, "forbidden", "invalid_auth"),
        (403, "premium_required", "premium_required"),
        (429, "rate_limited", "rate_limited"),
        (503, "internal_error", "cannot_connect"),
        (400, "invalid_request", "unknown"),
    ],
)
async def test_key_errors_show_and_recover(
    hass: HomeAssistant, nappi: FakeNappi, status: int, code: str, error: str
) -> None:
    nappi.fail("babies", status, code, {"Retry-After": "30"})
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_API_KEY: KEY})
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": error}

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_API_KEY: KEY})
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_connection_error(hass: HomeAssistant, nappi: FakeNappi) -> None:
    nappi.raise_once("babies", aiohttp.ClientConnectionError())
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_API_KEY: KEY})
    assert result["errors"] == {"base": "cannot_connect"}


async def test_malformed_key_is_rejected_without_a_request(hass: HomeAssistant, nappi: FakeNappi) -> None:
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_API_KEY: "abc123"})
    assert result["errors"] == {"base": "invalid_key_format"}
    assert nappi.calls == []


async def test_household_without_babies(hass: HomeAssistant, nappi: FakeNappi) -> None:
    nappi.babies.clear()
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_API_KEY: KEY})
    assert result["errors"] == {"base": "no_babies"}


async def test_reauth_swaps_the_key_for_every_baby_using_it(
    hass: HomeAssistant, nappi: FakeNappi, config_entry: MockConfigEntry
) -> None:
    nappi.babies.append({"id": TWIN_ID, "name": "Ada", "birth_date": None, "timezone": None})
    twin = MockConfigEntry(
        domain=DOMAIN,
        title="Ada",
        unique_id=TWIN_ID,
        data={CONF_API_KEY: KEY, CONF_BABY_ID: TWIN_ID, CONF_BABY_NAME: "Ada"},
    )
    twin.add_to_hass(hass)
    unrelated = MockConfigEntry(
        domain=DOMAIN,
        title="Other",
        unique_id="other",
        data={CONF_API_KEY: "nappi_" + "ef" * 24, CONF_BABY_ID: "other", CONF_BABY_NAME: "Other"},
    )
    unrelated.add_to_hass(hass)

    result = await config_entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_API_KEY: NEW_KEY})
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert config_entry.data[CONF_API_KEY] == NEW_KEY
    assert twin.data[CONF_API_KEY] == NEW_KEY
    assert unrelated.data[CONF_API_KEY] != NEW_KEY


async def test_reauth_rejects_a_key_from_another_household(
    hass: HomeAssistant, nappi: FakeNappi, config_entry: MockConfigEntry
) -> None:
    nappi.babies = [{"id": TWIN_ID, "name": "Ada", "birth_date": None, "timezone": None}]
    result = await config_entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_API_KEY: NEW_KEY})
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "baby_not_in_household"}
    assert config_entry.data[CONF_API_KEY] == KEY


async def test_reauth_with_a_bad_key(hass: HomeAssistant, nappi: FakeNappi, config_entry: MockConfigEntry) -> None:
    nappi.fail("babies", 401, "unauthorized")
    result = await config_entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_API_KEY: NEW_KEY})
    assert result["errors"] == {"base": "invalid_auth"}


async def test_options_set_the_bottle_unit(hass: HomeAssistant, setup_entry: MockConfigEntry) -> None:
    result = await hass.config_entries.options.async_init(setup_entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(result["flow_id"], {CONF_BOTTLE_UNIT: "oz"})
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert setup_entry.options == {CONF_BOTTLE_UNIT: "oz"}
    # The entry reloaded with the new unit.
    state = hass.states.get("number.phoebe_bottle_amount")
    assert state.attributes["unit_of_measurement"] == "fl. oz."
    assert state.state == "4"
