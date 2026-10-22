"""One-press logs: diapers and a bottle."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import NappiConfigEntry, NappiRuntimeData
from .const import BOTTLE_TYPE_UNSPECIFIED, CONF_BOTTLE_UNIT, DEFAULT_BOTTLE_UNIT, DOMAIN
from .entity import NappiEntity, async_call_api

PARALLEL_UPDATES = 1


@dataclass(frozen=True, kw_only=True)
class NappiButtonDescription(ButtonEntityDescription):
    """A button that logs one entry."""

    diaper_type: str | None = None


BUTTONS: tuple[NappiButtonDescription, ...] = (
    NappiButtonDescription(key="diaper_wet", diaper_type="wet"),
    NappiButtonDescription(key="diaper_dirty", diaper_type="poo"),
    NappiButtonDescription(key="diaper_mixed", diaper_type="mixed"),
    NappiButtonDescription(key="log_bottle"),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NappiConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    runtime = entry.runtime_data
    unit = entry.options.get(CONF_BOTTLE_UNIT, DEFAULT_BOTTLE_UNIT)
    async_add_entities(NappiLogButton(runtime, description, unit) for description in BUTTONS)


class NappiLogButton(NappiEntity, ButtonEntity):
    """Logs a diaper or a bottle when pressed."""

    entity_description: NappiButtonDescription

    def __init__(self, runtime: NappiRuntimeData, description: NappiButtonDescription, unit: str) -> None:
        super().__init__(runtime.coordinator, description.key)
        self.entity_description = description
        self._runtime = runtime
        self._unit = unit

    @property
    def available(self) -> bool:
        # Logging does not depend on the last poll: a rate-limited or failed
        # read must not stop someone logging a diaper.
        return True

    def _payload(self) -> dict[str, Any]:
        if self.entity_description.diaper_type:
            return {"type": "diaper", "diaper_type": self.entity_description.diaper_type}
        amount = self._runtime.bottle_amount
        if amount is None or amount <= 0:
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="no_bottle_amount")
        payload: dict[str, Any] = {"type": "bottle", "amount": amount, "unit": self._unit}
        if self._runtime.bottle_type != BOTTLE_TYPE_UNSPECIFIED:
            payload["bottle_type"] = self._runtime.bottle_type
        return payload

    async def async_press(self) -> None:
        coordinator = self.coordinator
        await async_call_api(coordinator, coordinator.client.async_log_entry(coordinator.baby_id, self._payload()))
        await coordinator.async_request_refresh()
