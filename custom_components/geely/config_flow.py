"""Email-code setup. Geely does not take a password for this account."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector

from .cloud.client import Device, GeelyError
from .cloud.regions import COUNTRIES, DEFAULT_COUNTRY, RegionUnavailable
from .const import (
    CONF_ACTIVE_SECONDS,
    CONF_CODE,
    CONF_COUNTRY,
    CONF_EMAIL,
    CONF_PARKED_SECONDS,
    DEFAULT_ACTIVE_SECONDS,
    DEFAULT_PARKED_SECONDS,
    DOMAIN,
)
from .session import complete_sign_in, send_code

_LOGGER = logging.getLogger(__name__)

_COUNTRY_OPTIONS = [
    {"label": name, "value": code}
    for code, name in sorted(COUNTRIES.items(), key=lambda item: item[1])
]


def _start_schema() -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_EMAIL): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.EMAIL)
            ),
            vol.Required(CONF_COUNTRY, default=DEFAULT_COUNTRY): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=_COUNTRY_OPTIONS,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            ),
        }
    )


def _code_schema() -> vol.Schema:
    return vol.Schema({vol.Required(CONF_CODE): str})


def _options_schema(entry: config_entries.ConfigEntry) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(
                CONF_ACTIVE_SECONDS,
                default=int(entry.options.get(
                    CONF_ACTIVE_SECONDS, DEFAULT_ACTIVE_SECONDS)),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(min=30, max=300, step=1, mode="box")
            ),
            vol.Required(
                CONF_PARKED_SECONDS,
                default=int(entry.options.get(
                    CONF_PARKED_SECONDS, DEFAULT_PARKED_SECONDS)),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(min=30, max=3600, step=1, mode="box")
            ),
        }
    )


def _error_key(err: Exception) -> str:
    if isinstance(err, RegionUnavailable):
        return "region"
    if isinstance(err, GeelyError):
        if err.kind in ("network", "tls", "captcha_unreachable"):
            return "cannot_connect"
        if err.kind == "bad_code":
            return "invalid_code"
        if err.kind == "no_vehicles":
            return "no_vehicles"
        if err.kind == "send_failed":
            return "cannot_send"
        if err.kind == "auth":
            return "invalid_auth"
    return "unknown"


class GeelyConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Ask for the email, send one code, then open the cars."""

    VERSION = 1

    def __init__(self) -> None:
        self._email = ""
        self._country = DEFAULT_COUNTRY
        self._device = Device()
        self._reauth = False

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            email = str(user_input[CONF_EMAIL]).strip()
            country = str(user_input[CONF_COUNTRY]).strip()
            if "@" not in email or country not in COUNTRIES:
                errors["base"] = "invalid_auth"
            else:
                await self.async_set_unique_id(email.lower())
                self._abort_if_unique_id_configured()
                self._email = email
                self._country = country
                self._device = Device()
                try:
                    await self.hass.async_add_executor_job(
                        send_code, email, country, self._device)
                except Exception as err:  # noqa: BLE001
                    _LOGGER.warning("code request failed: %s (%s)", _error_key(err), type(err).__name__)
                    errors["base"] = _error_key(err)
                else:
                    return await self.async_step_code()
        return self.async_show_form(
            step_id="user", data_schema=_start_schema(), errors=errors)

    async def async_step_code(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            code = str(user_input[CONF_CODE]).replace(" ", "")
            try:
                data = await self.hass.async_add_executor_job(
                    complete_sign_in, self._email, code, self._country, self._device)
            except Exception as err:  # noqa: BLE001
                key = _error_key(err)
                _LOGGER.warning("sign-in failed: %s (%s)", key, type(err).__name__)
                if key == "no_vehicles":
                    return self.async_abort(reason="no_vehicles")
                errors["base"] = key
            else:
                if self._reauth:
                    return self.async_update_reload_and_abort(
                        self._get_reauth_entry(), data=data)
                return self.async_create_entry(title=f"Geely ({self._email})", data=data)
        return self.async_show_form(
            step_id="code", data_schema=_code_schema(), errors=errors)

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> FlowResult:
        self._reauth = True
        self._email = str(entry_data[CONF_EMAIL])
        self._country = str(entry_data[CONF_COUNTRY])
        self._device = Device()
        try:
            await self.hass.async_add_executor_job(
                send_code, self._email, self._country, self._device)
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("reauth code request failed: %s", _error_key(err))
            return self.async_abort(reason=_error_key(err))
        return await self.async_step_code()

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> GeelyOptionsFlow:
        return GeelyOptionsFlow()


class GeelyOptionsFlow(config_entries.OptionsFlow):
    """How often to read the car while it is in use, and while it is parked."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        if user_input is not None:
            return self.async_create_entry(
                title="",
                data={
                    CONF_ACTIVE_SECONDS: int(user_input[CONF_ACTIVE_SECONDS]),
                    CONF_PARKED_SECONDS: int(user_input[CONF_PARKED_SECONDS]),
                },
            )
        return self.async_show_form(
            step_id="init", data_schema=_options_schema(self.config_entry))
