"""Polling coordinator for one baby."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
import logging
from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    NappiAuthError,
    NappiBabyNotFoundError,
    NappiClient,
    NappiError,
    NappiPremiumRequiredError,
    NappiRateLimitedError,
)
from .const import (
    CONF_BABY_ID,
    CONF_BABY_NAME,
    DOMAIN,
    ISSUE_BABY_NOT_FOUND,
    ISSUE_PREMIUM_REQUIRED,
    KEYS_URL,
    SCAN_INTERVAL,
)

if TYPE_CHECKING:
    from . import NappiConfigEntry

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class NappiData:
    """What one poll knows about a baby."""

    latest: dict[str, Any] = field(default_factory=dict)
    timers: dict[str, dict[str, Any]] = field(default_factory=dict)


def premium_issue_id(entry_id: str) -> str:
    return f"{ISSUE_PREMIUM_REQUIRED}_{entry_id}"


def baby_issue_id(entry_id: str) -> str:
    return f"{ISSUE_BABY_NOT_FOUND}_{entry_id}"


class NappiCoordinator(DataUpdateCoordinator[NappiData]):
    """Polls the running timers and the latest entries every two minutes, in one request."""

    config_entry: NappiConfigEntry

    def __init__(self, hass: HomeAssistant, entry: NappiConfigEntry, client: NappiClient) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {entry.data[CONF_BABY_NAME]}",
            update_interval=SCAN_INTERVAL,
        )
        self.client = client
        self.baby_id: str = entry.data[CONF_BABY_ID]
        self.baby_name: str = entry.data[CONF_BABY_NAME]
        self._etag: str | None = None

    async def _async_update_data(self) -> NappiData:
        entry_id = self.config_entry.entry_id
        try:
            result = await self.client.async_get_status(self.baby_id, self._etag)
        except NappiAuthError as err:
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN,
                translation_key="auth_failed",
            ) from err
        except NappiPremiumRequiredError as err:
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                premium_issue_id(entry_id),
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key=ISSUE_PREMIUM_REQUIRED,
                translation_placeholders={"baby": self.baby_name},
                learn_more_url=KEYS_URL,
            )
            raise UpdateFailed(translation_domain=DOMAIN, translation_key="premium_required") from err
        except NappiBabyNotFoundError as err:
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                baby_issue_id(entry_id),
                is_fixable=False,
                severity=ir.IssueSeverity.ERROR,
                translation_key=ISSUE_BABY_NOT_FOUND,
                translation_placeholders={"baby": self.baby_name},
            )
            raise UpdateFailed(translation_domain=DOMAIN, translation_key="baby_not_found") from err
        except NappiRateLimitedError as err:
            # Wait as long as the server asks before the next poll.
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="rate_limited",
                translation_placeholders={"seconds": str(err.retry_after)},
                retry_after=err.retry_after,
            ) from err
        except NappiError as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="update_failed",
                translation_placeholders={"error": str(err)},
            ) from err

        ir.async_delete_issue(self.hass, DOMAIN, premium_issue_id(entry_id))
        ir.async_delete_issue(self.hass, DOMAIN, baby_issue_id(entry_id))

        self._etag = result.etag
        if result.not_modified:
            return self.data or NappiData()
        return NappiData(
            latest=result.latest or {},
            timers={
                timer["type"]: timer for timer in result.timers or [] if isinstance(timer, dict) and "type" in timer
            },
        )

    def set_timer(self, timer_type: str, timer: dict[str, Any] | None) -> None:
        """Apply a timer change we just made, without waiting for the next poll."""
        # The local data no longer matches the ETag, so the next poll must not
        # accept a 304 for it (a timer discarded on the phone saves no entry).
        self._etag = None
        current = self.data or NappiData()
        timers = dict(current.timers)
        if timer is None:
            timers.pop(timer_type, None)
        else:
            timers[timer_type] = timer
        self.async_set_updated_data(replace(current, timers=timers))
