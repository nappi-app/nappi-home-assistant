"""Shared entity base and write helpers."""

from __future__ import annotations

from collections.abc import Awaitable
from typing import Any

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import (
    NappiAuthError,
    NappiBabyNotFoundError,
    NappiError,
    NappiPremiumRequiredError,
    NappiRateLimitedError,
)
from .const import DOMAIN, KEYS_URL, VERSION
from .coordinator import NappiCoordinator


class NappiEntity(CoordinatorEntity[NappiCoordinator]):
    """An entity on the baby's device."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: NappiCoordinator, key: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.baby_id}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.baby_id)},
            name=coordinator.baby_name,
            manufacturer="nappi",
            model="Baby",
            sw_version=VERSION,
            entry_type=DeviceEntryType.SERVICE,
            configuration_url=KEYS_URL,
        )


async def async_call_api[T](coordinator: NappiCoordinator, call: Awaitable[T]) -> T:
    """Run a write and turn API errors into messages the user can act on."""
    try:
        return await call
    except NappiAuthError as err:
        coordinator.config_entry.async_start_reauth(coordinator.hass)
        raise HomeAssistantError(translation_domain=DOMAIN, translation_key="auth_failed") from err
    except NappiPremiumRequiredError as err:
        raise HomeAssistantError(translation_domain=DOMAIN, translation_key="premium_required") from err
    except NappiBabyNotFoundError as err:
        raise HomeAssistantError(translation_domain=DOMAIN, translation_key="baby_not_found") from err
    except NappiRateLimitedError as err:
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="rate_limited",
            translation_placeholders={"seconds": str(err.retry_after)},
        ) from err
    except NappiError as err:
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="request_failed",
            translation_placeholders={"error": str(err)},
        ) from err


def without(entry: dict[str, Any], *keys: str) -> dict[str, Any]:
    """Copy an API object without the given keys."""
    return {k: v for k, v in entry.items() if k not in keys}
