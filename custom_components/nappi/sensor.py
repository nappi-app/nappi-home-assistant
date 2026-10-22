"""The latest entry of each type."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from . import NappiConfigEntry
from .coordinator import NappiCoordinator
from .entity import NappiEntity, without

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class NappiLatestDescription(SensorEntityDescription):
    """Latest entry of one type. ``time_field`` holds the sensor's state."""

    entry_type: str
    time_field: str = "time"


def _latest(entry_type: str, time_field: str = "time", enabled: bool = True) -> NappiLatestDescription:
    return NappiLatestDescription(
        key=f"last_{entry_type}",
        entry_type=entry_type,
        time_field=time_field,
        device_class=SensorDeviceClass.DATE if time_field == "date" else SensorDeviceClass.TIMESTAMP,
        entity_registry_enabled_default=enabled,
    )


SENSORS: tuple[NappiLatestDescription, ...] = (
    # A sleep's state is when it ended (the baby woke up); start_time is an attribute.
    _latest("sleep", "end_time"),
    _latest("nursing"),
    _latest("bottle"),
    _latest("diaper"),
    _latest("pumping"),
    _latest("potty", enabled=False),
    _latest("solids", enabled=False),
    _latest("activity", enabled=False),
    _latest("growth", "date", enabled=False),
    _latest("medication", enabled=False),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NappiConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data.coordinator
    async_add_entities(NappiLatestSensor(coordinator, description) for description in SENSORS)


class NappiLatestSensor(NappiEntity, SensorEntity):
    """When the newest entry of a type happened, with its details as attributes."""

    entity_description: NappiLatestDescription

    def __init__(self, coordinator: NappiCoordinator, description: NappiLatestDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def _entry(self) -> dict[str, Any] | None:
        data = self.coordinator.data
        entry = data.latest.get(self.entity_description.entry_type) if data else None
        return entry if isinstance(entry, dict) else None

    @property
    def native_value(self) -> datetime | date | None:
        entry = self._entry
        if entry is None:
            return None
        raw = entry.get(self.entity_description.time_field)
        if not isinstance(raw, str):
            return None
        if self.entity_description.time_field == "date":
            return dt_util.parse_date(raw[:10])
        return dt_util.parse_datetime(raw)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        entry = self._entry
        if entry is None:
            return None
        attributes = without(entry, "id", self.entity_description.time_field)
        attributes["entry_id"] = entry.get("id")
        return attributes
