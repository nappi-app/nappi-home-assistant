"""Amounts used by the "Log bottle" and "Log pumping" buttons."""

from __future__ import annotations

from homeassistant.components.number import NumberMode, RestoreNumber
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import NappiConfigEntry, NappiRuntimeData
from .const import CONF_BOTTLE_UNIT, DEFAULT_BOTTLE_UNIT, UNIT_OZ
from .entity import NappiEntity

PARALLEL_UPDATES = 0

# (min, max, step, default) per unit. The API accepts up to 1000 ml.
RANGES: dict[str, tuple[float, float, float, float]] = {
    "ml": (5, 500, 5, 120),
    "oz": (0.25, 16, 0.25, 4),
}
UNIT_LABELS = {"ml": "mL", "oz": "fl. oz."}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NappiConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    unit = entry.options.get(CONF_BOTTLE_UNIT, DEFAULT_BOTTLE_UNIT)
    runtime = entry.runtime_data
    async_add_entities([NappiAmount(runtime, "bottle_amount", unit), NappiAmount(runtime, "pumping_amount", unit)])


class NappiAmount(NappiEntity, RestoreNumber):
    """How much the bottle or pumping button logs. Kept across restarts, local only.

    ``key`` is both the entity key and the NappiRuntimeData field the button reads.
    """

    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.BOX

    def __init__(self, runtime: NappiRuntimeData, key: str, unit: str) -> None:
        super().__init__(runtime.coordinator, key)
        self._runtime = runtime
        self._key = key
        self._unit = UNIT_OZ if unit == UNIT_OZ else "ml"
        low, high, step, default = RANGES[self._unit]
        self._attr_native_min_value = low
        self._attr_native_max_value = high
        self._attr_native_step = step
        self._attr_native_unit_of_measurement = UNIT_LABELS[self._unit]
        self._attr_native_value = default

    @property
    def available(self) -> bool:
        return True

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_number_data()
        if (
            last is not None
            and last.native_value is not None
            # A value saved in the other unit is meaningless now: keep the default.
            and last.native_unit_of_measurement == self._attr_native_unit_of_measurement
            and self.native_min_value <= last.native_value <= self.native_max_value
        ):
            self._attr_native_value = last.native_value
        setattr(self._runtime, self._key, self._attr_native_value)

    async def async_set_native_value(self, value: float) -> None:
        self._attr_native_value = value
        setattr(self._runtime, self._key, value)
        self.async_write_ha_state()
