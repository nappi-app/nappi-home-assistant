"""What the "Log bottle" button records the bottle as."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from . import NappiConfigEntry, NappiRuntimeData
from .const import BOTTLE_TYPE_UNSPECIFIED, BOTTLE_TYPES
from .entity import NappiEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NappiConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([NappiBottleContents(entry.runtime_data)])


class NappiBottleContents(NappiEntity, SelectEntity, RestoreEntity):
    """Bottle contents for the bottle button. Kept across restarts, local only."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_options = [BOTTLE_TYPE_UNSPECIFIED, *BOTTLE_TYPES]

    def __init__(self, runtime: NappiRuntimeData) -> None:
        super().__init__(runtime.coordinator, "bottle_contents")
        self._runtime = runtime
        self._attr_current_option = BOTTLE_TYPE_UNSPECIFIED

    @property
    def available(self) -> bool:
        return True

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None and last.state in self._attr_options:
            self._attr_current_option = last.state
        self._runtime.bottle_type = self._attr_current_option or BOTTLE_TYPE_UNSPECIFIED

    async def async_select_option(self, option: str) -> None:
        self._attr_current_option = option
        self._runtime.bottle_type = option
        self.async_write_ha_state()
