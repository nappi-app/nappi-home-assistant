"""Sleep, nursing and pumping timers as switches, and a pause switch for each."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import NappiConfigEntry
from .api import NappiTimerNotRunningError
from .const import TIMER_TYPES
from .coordinator import NappiCoordinator
from .entity import NappiEntity, async_call_api, async_start_timer, async_timer_action, without

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NappiConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data.coordinator
    async_add_entities(NappiTimerSwitch(coordinator, timer_type) for timer_type in TIMER_TYPES)
    async_add_entities(NappiTimerPausedSwitch(coordinator, timer_type) for timer_type in TIMER_TYPES)


class NappiTimerSwitch(NappiEntity, SwitchEntity):
    """On while the household's timer runs, wherever it was started."""

    def __init__(self, coordinator: NappiCoordinator, timer_type: str) -> None:
        super().__init__(coordinator, f"{timer_type}_timer")
        self._timer_type = timer_type

    @property
    def _timer(self) -> dict[str, Any] | None:
        data = self.coordinator.data
        return data.timers.get(self._timer_type) if data else None

    @property
    def is_on(self) -> bool:
        return self._timer is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        timer = self._timer
        if timer is None:
            return None
        # elapsed_seconds is as of the last poll; started_at is exact.
        return without(timer, "type")

    async def async_turn_on(self, **kwargs: Any) -> None:
        await async_start_timer(self.coordinator, self._timer_type)

    async def async_turn_off(self, **kwargs: Any) -> None:
        coordinator = self.coordinator

        async def stop() -> None:
            try:
                await coordinator.client.async_stop_timer(coordinator.baby_id, self._timer_type)
            except NappiTimerNotRunningError:
                # Already stopped elsewhere (the phone, a watch, Alexa).
                pass

        await async_call_api(coordinator, stop())
        coordinator.set_timer(self._timer_type, None)
        # Stopping saved an entry, so the latest-entry sensors changed too.
        await coordinator.async_request_refresh()


class NappiTimerPausedSwitch(NappiEntity, SwitchEntity):
    """On while the household's timer is paused. Turning it off resumes the timer.

    A paused nursing timer freezes both sides and resumes on the side that was
    running. A paused sleep or pumping timer is stopped but not saved yet, as
    in the app: turning off the timer switch saves it as of the pause.
    """

    def __init__(self, coordinator: NappiCoordinator, timer_type: str) -> None:
        super().__init__(coordinator, f"{timer_type}_timer_paused")
        self._timer_type = timer_type
        # Pausing a feed is common; pausing sleep or pumping is not, so those
        # start disabled and can be turned on from the device page.
        self._attr_entity_registry_enabled_default = timer_type == "nursing"

    @property
    def is_on(self) -> bool:
        data = self.coordinator.data
        timer = data.timers.get(self._timer_type) if data else None
        return bool(timer and timer.get("paused"))

    async def async_turn_on(self, **kwargs: Any) -> None:
        coordinator = self.coordinator
        await async_timer_action(
            coordinator, self._timer_type, coordinator.client.async_pause_timer(coordinator.baby_id, self._timer_type)
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        coordinator = self.coordinator
        await async_timer_action(
            coordinator, self._timer_type, coordinator.client.async_resume_timer(coordinator.baby_id, self._timer_type)
        )
