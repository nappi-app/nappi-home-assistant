"""One-press actions: log diapers, a bottle and pumping, start nursing on a side, switch sides."""

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
from .entity import NappiEntity, async_call_api, async_start_timer, async_timer_action

PARALLEL_UPDATES = 1


@dataclass(frozen=True, kw_only=True)
class NappiButtonDescription(ButtonEntityDescription):
    """A button that logs one entry, starts nursing on one side, or switches sides."""

    diaper_type: str | None = None
    nursing_side: str | None = None
    pumping: bool = False
    switch_side: bool = False


BUTTONS: tuple[NappiButtonDescription, ...] = (
    NappiButtonDescription(key="diaper_wet", diaper_type="wet"),
    NappiButtonDescription(key="diaper_dirty", diaper_type="poo"),
    NappiButtonDescription(key="diaper_mixed", diaper_type="mixed"),
    NappiButtonDescription(key="log_bottle"),
    NappiButtonDescription(key="log_pumping", pumping=True),
    NappiButtonDescription(key="start_nursing_left", nursing_side="left"),
    NappiButtonDescription(key="start_nursing_right", nursing_side="right"),
    NappiButtonDescription(key="start_nursing_both", nursing_side="both"),
    NappiButtonDescription(key="switch_nursing_side", switch_side=True),
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
    """Logs a diaper, a bottle or pumping, or starts nursing on a side, when pressed."""

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

    async def _switch_side(self) -> None:
        coordinator = self.coordinator

        def nursing() -> dict[str, Any] | None:
            return coordinator.data.timers.get("nursing") if coordinator.data else None

        timer = nursing()
        if timer is None:
            # The last poll can be two minutes old: a feed started on the
            # phone since then is not in it yet.
            await coordinator.async_refresh()
            timer = nursing()
        if timer is None:
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="timer_not_running")
        side = timer.get("side")
        if side not in ("left", "right"):
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="timer_has_no_side")
        # Only that feed: if it ended and another began, the server answers
        # timer_changed instead of switching a feed we never saw. If the side
        # changed on the phone since the poll, switching to it changes nothing.
        await async_timer_action(
            coordinator,
            "nursing",
            coordinator.client.async_switch_nursing_side(
                coordinator.baby_id, "right" if side == "left" else "left", timer.get("id")
            ),
        )

    async def async_press(self) -> None:
        coordinator = self.coordinator
        if self.entity_description.switch_side:
            await self._switch_side()
            return
        if self.entity_description.pumping:
            await self._log_pumping()
            return
        if side := self.entity_description.nursing_side:
            # Nursing that already runs keeps its side and seconds: the server
            # ignores the new side, and so do we.
            await async_start_timer(coordinator, "nursing", {"side": side})
            return
        await async_call_api(coordinator, coordinator.client.async_log_entry(coordinator.baby_id, self._payload()))
        await coordinator.async_request_refresh()
