"""Config flow for Brunata Online — username/password only, no YAML."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.data_entry_flow import FlowResult

from brunata_client import BrunataClient
from brunata_client.exceptions import BrunataLoginError

from .const import DOMAIN

_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
    }
)


async def _validate_credentials(username: str, password: str) -> None:
    """Raise BrunataLoginError on bad credentials; propagate other errors as-is."""
    client = BrunataClient(username=username, password=password)
    try:
        await client.login()
    finally:
        await client.close()


class BrunataConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Brunata Online."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                await _validate_credentials(
                    user_input[CONF_USERNAME], user_input[CONF_PASSWORD]
                )
            except BrunataLoginError:
                errors["base"] = "invalid_auth"
            except Exception:  # unexpected transport/protocol failure
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(user_input[CONF_USERNAME])
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=user_input[CONF_USERNAME], data=user_input
                )

        return self.async_show_form(
            step_id="user", data_schema=_SCHEMA, errors=errors
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Let the user update their password without removing the integration."""
        errors: dict[str, str] = {}
        entry = self._get_reconfigure_entry()

        if user_input is not None:
            try:
                await _validate_credentials(
                    user_input[CONF_USERNAME], user_input[CONF_PASSWORD]
                )
            except BrunataLoginError:
                errors["base"] = "invalid_auth"
            except Exception:  # unexpected transport/protocol failure
                errors["base"] = "unknown"
            else:
                return self.async_update_reload_and_abort(entry, data=user_input)

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(_SCHEMA, entry.data),
            errors=errors,
        )
