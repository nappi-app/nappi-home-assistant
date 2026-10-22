"""Set up nappi: paste a connection key, pick a baby."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.const import CONF_API_KEY
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import (
    Baby,
    NappiAuthError,
    NappiClient,
    NappiConnectionError,
    NappiError,
    NappiPremiumRequiredError,
    NappiRateLimitedError,
)
from .const import (
    CONF_BABY_ID,
    CONF_BABY_NAME,
    CONF_BOTTLE_UNIT,
    DEFAULT_BOTTLE_UNIT,
    DOMAIN,
    KEY_PATTERN,
    KEYS_URL,
    UNIT_ML,
    UNIT_OZ,
)

_LOGGER = logging.getLogger(__name__)

KEY_SCHEMA = vol.Schema({vol.Required(CONF_API_KEY): TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))})


def _clean_key(raw: str) -> str:
    key = raw.strip()
    # People paste the whole header value from the docs page sometimes.
    if key.lower().startswith("bearer "):
        key = key[7:].strip()
    return key


class NappiConfigFlow(ConfigFlow, domain=DOMAIN):
    """Config flow for nappi."""

    VERSION = 1

    def __init__(self) -> None:
        self._api_key: str | None = None
        self._babies: list[Baby] = []

    async def _async_fetch_babies(self, api_key: str) -> tuple[list[Baby] | None, str | None]:
        """Return (babies, error key)."""
        if not KEY_PATTERN.match(api_key):
            return None, "invalid_key_format"
        client = NappiClient(async_get_clientsession(self.hass), api_key)
        try:
            return await client.async_get_babies(), None
        except NappiAuthError:
            return None, "invalid_auth"
        except NappiPremiumRequiredError:
            return None, "premium_required"
        except NappiRateLimitedError:
            return None, "rate_limited"
        except NappiConnectionError:
            return None, "cannot_connect"
        except NappiError:
            _LOGGER.exception("Unexpected nappi API error")
            return None, "unknown"

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask for a connection key."""
        errors: dict[str, str] = {}
        if user_input is not None:
            api_key = _clean_key(user_input[CONF_API_KEY])
            babies, error = await self._async_fetch_babies(api_key)
            if error:
                errors["base"] = error
            elif not babies:
                errors["base"] = "no_babies"
            else:
                configured = self._async_current_ids(include_ignore=False)
                available = [baby for baby in babies if baby.id not in configured]
                if not available:
                    return self.async_abort(reason="already_configured")
                self._api_key = api_key
                self._babies = available
                if len(available) == 1:
                    return await self._async_create(available[0])
                return await self.async_step_baby()

        return self.async_show_form(
            step_id="user",
            data_schema=KEY_SCHEMA,
            errors=errors,
            description_placeholders={"keys_url": KEYS_URL},
        )

    async def async_step_baby(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Pick which baby this entry is for."""
        if user_input is not None:
            baby = next((b for b in self._babies if b.id == user_input[CONF_BABY_ID]), None)
            if baby is not None:
                return await self._async_create(baby)

        options = [SelectOptionDict(value=baby.id, label=baby.name) for baby in self._babies]
        return self.async_show_form(
            step_id="baby",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_BABY_ID): SelectSelector(
                        SelectSelectorConfig(options=options, mode=SelectSelectorMode.LIST)
                    )
                }
            ),
        )

    async def _async_create(self, baby: Baby) -> ConfigFlowResult:
        await self.async_set_unique_id(baby.id)
        self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title=baby.name,
            data={CONF_API_KEY: self._api_key, CONF_BABY_ID: baby.id, CONF_BABY_NAME: baby.name},
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """The key stopped working (expired, revoked, or its owner left the household)."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask for a new connection key."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            api_key = _clean_key(user_input[CONF_API_KEY])
            babies, error = await self._async_fetch_babies(api_key)
            if error:
                errors["base"] = error
            elif not any(baby.id == entry.data[CONF_BABY_ID] for baby in babies or []):
                errors["base"] = "baby_not_in_household"
            else:
                self._async_update_siblings(entry, api_key)
                return self.async_update_reload_and_abort(entry, data_updates={CONF_API_KEY: api_key})

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=KEY_SCHEMA,
            errors=errors,
            description_placeholders={"baby": entry.title, "keys_url": KEYS_URL},
        )

    @callback
    def _async_update_siblings(self, entry: ConfigEntry, api_key: str) -> None:
        """Other babies set up with the same old key get the new key too."""
        old_key = entry.data[CONF_API_KEY]
        for other in self._async_current_entries(include_ignore=False):
            if other.entry_id == entry.entry_id or other.data.get(CONF_API_KEY) != old_key:
                continue
            self.hass.config_entries.async_update_entry(other, data={**other.data, CONF_API_KEY: api_key})
            self.hass.config_entries.async_schedule_reload(other.entry_id)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> NappiOptionsFlow:
        return NappiOptionsFlow()


class NappiOptionsFlow(OptionsFlowWithReload):
    """Bottle unit."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        current = self.config_entry.options.get(CONF_BOTTLE_UNIT, DEFAULT_BOTTLE_UNIT)
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_BOTTLE_UNIT, default=current): SelectSelector(
                        SelectSelectorConfig(
                            options=[UNIT_ML, UNIT_OZ],
                            translation_key=CONF_BOTTLE_UNIT,
                            mode=SelectSelectorMode.LIST,
                        )
                    )
                }
            ),
        )
