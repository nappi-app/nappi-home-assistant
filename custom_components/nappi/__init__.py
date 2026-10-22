"""The nappi integration: log entries and run timers for a baby in nappi."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_API_KEY, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import NappiClient
from .const import BOTTLE_TYPE_UNSPECIFIED, DOMAIN
from .coordinator import NappiCoordinator, baby_issue_id, premium_issue_id

PLATFORMS: list[Platform] = [
    Platform.BUTTON,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
]


@dataclass(slots=True)
class NappiRuntimeData:
    """Per-entry state shared by the platforms."""

    coordinator: NappiCoordinator
    # The bottle button logs whatever the "Bottle amount" and "Bottle contents"
    # entities hold. They write here so the button never reads entity state.
    bottle_amount: float | None = None
    bottle_type: str = BOTTLE_TYPE_UNSPECIFIED


type NappiConfigEntry = ConfigEntry[NappiRuntimeData]


async def async_setup_entry(hass: HomeAssistant, entry: NappiConfigEntry) -> bool:
    """Set up nappi for one baby."""
    client = NappiClient(async_get_clientsession(hass), entry.data[CONF_API_KEY])
    coordinator = NappiCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = NappiRuntimeData(coordinator=coordinator)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: NappiConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: NappiConfigEntry) -> None:
    """Clear this entry's repair issues when it is deleted."""
    ir.async_delete_issue(hass, DOMAIN, premium_issue_id(entry.entry_id))
    ir.async_delete_issue(hass, DOMAIN, baby_issue_id(entry.entry_id))
