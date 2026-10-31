"""One-press logs: diapers, a bottle and pumping."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import NappiConfigEntry, NappiRuntimeData
from .api import NappiTimerNotRunningError
from .const import BOTTLE_TYPE_UNSPECIFIED, CONF_BOTTLE_UNIT, DEFAULT_BOTTLE_UNIT, DOMAIN
from .entity import NappiEntity, async_call_api

PARALLEL_UPDATES = 1


@dataclass(frozen=True, kw_only=True)
class NappiButtonDescription(ButtonEntityDescription):
    """A button that logs one entry."""

    diaper_type: str | None = None
    pumping: bool = False


BUTTONS: tuple[NappiButtonDescription, ...] = (
    NappiButtonDescription(key="diaper_wet", diaper_type="wet"),
    NappiButtonDescription(key="diaper_dirty", diaper_type="poo"),
    NappiButtonDescription(key="diaper_mixed", diaper_type="mixed"),
    NappiButtonDescription(key="log_bottle"),
    NappiButtonDescription(key="log_pumping", pumping=True),
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
    """Logs a diaper, a bottle or pumping when pressed."""

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

    async def _log_pumping(self) -> None:
        coordinator = self.coordinator
        amount = self._runtime.pumping_amount
        if amount is None or amount <= 0:
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="no_pumping_amount")
        body: dict[str, Any] = {"amount": amount, "unit": self._unit}

        async def save() -> None:
            try:
                # A running timer is stopped with the amount, so one entry has
                # both the duration and the amount. The poll can be two minutes
                # old, so ask the server instead of trusting what it showed.
                await coordinator.client.async_stop_timer(coordinator.baby_id, "pumping", body)
            except NappiTimerNotRunningError:
                await coordinator.client.async_log_entry(coordinator.baby_id, {"type": "pumping", **body})

        await async_call_api(coordinator, save())
        coordinator.set_timer("pumping", None)
        await coordinator.async_request_refresh()

    async def async_press(self) -> None:
        coordinator = self.coordinator
        if self.entity_description.pumping:
            await self._log_pumping()
            return
        await async_call_api(coordinator, coordinator.client.async_log_entry(coordinator.baby_id, self._payload()))
        await coordinator.async_request_refresh()
