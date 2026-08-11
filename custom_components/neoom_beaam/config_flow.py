"""Config flow for the neoom BEAAM integration."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_HOST
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import BeaamApiClient, BeaamAuthError, BeaamConnectionError
from .const import CONF_API_KEY, CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL, DOMAIN
from .coordinator import extract_site_id

_LOGGER = logging.getLogger(__name__)

SCAN_INTERVAL_SELECTOR = vol.All(int, vol.Range(min=5, max=300))

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Required(CONF_API_KEY): str,
        vol.Optional(CONF_SCAN_INTERVAL, default=DEFAULT_SCAN_INTERVAL): SCAN_INTERVAL_SELECTOR,
    }
)


class NeoomBeaamConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle initial setup, re-authentication and reconfiguration."""

    VERSION = 1

    async def _async_validate(self, host: str, api_key: str) -> tuple[str | None, str | None]:
        """Probe the BEAAM.

        Returns (site_id, error_key). site_id is None when the firmware does not
        report one — the caller then falls back to the host as unique_id.
        """
        session = async_get_clientsession(self.hass)
        client = BeaamApiClient(host=host, api_key=api_key, session=session)
        try:
            config = await client.get_site_configuration()
        except BeaamAuthError:
            return None, "invalid_auth"
        except BeaamConnectionError:
            return None, "cannot_connect"
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Unexpected error while validating the BEAAM connection")
            return None, "unknown"
        return extract_site_id(config), None

    # ------------------------------------------------------------------
    # Initial setup
    # ------------------------------------------------------------------

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            host = user_input[CONF_HOST].strip().rstrip("/")
            api_key = user_input[CONF_API_KEY].strip()

            site_id, error = await self._async_validate(host, api_key)
            if error:
                errors["base"] = error
            else:
                # Prefer the site id so the entry survives an IP change.
                await self.async_set_unique_id(site_id or host)
                self._abort_if_unique_id_configured()

                return self.async_create_entry(
                    title=f"neoom BEAAM ({host})",
                    data={
                        CONF_HOST: host,
                        CONF_API_KEY: api_key,
                        CONF_SCAN_INTERVAL: user_input[CONF_SCAN_INTERVAL],
                    },
                )

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_SCHEMA,
            errors=errors,
        )

    # ------------------------------------------------------------------
    # Re-authentication (expired or revoked API key)
    # ------------------------------------------------------------------

    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> config_entries.FlowResult:
        """Triggered when the integration raises ConfigEntryAuthFailed."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        entry = self._existing_entry()
        if entry is None:
            return self.async_abort(reason="reauth_failed")

        errors: dict[str, str] = {}

        if user_input is not None:
            api_key = user_input[CONF_API_KEY].strip()
            host = entry.data[CONF_HOST]

            _site_id, error = await self._async_validate(host, api_key)
            if error:
                errors["base"] = error
            else:
                self.hass.config_entries.async_update_entry(
                    entry, data={**entry.data, CONF_API_KEY: api_key}
                )
                await self.hass.config_entries.async_reload(entry.entry_id)
                return self.async_abort(reason="reauth_successful")

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_API_KEY): str}),
            description_placeholders={"host": entry.data[CONF_HOST]},
            errors=errors,
        )

    # ------------------------------------------------------------------
    # Reconfiguration (e.g. the BEAAM got a new IP address)
    # ------------------------------------------------------------------

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        entry = self._existing_entry()
        if entry is None:
            return self.async_abort(reason="reconfigure_failed")

        errors: dict[str, str] = {}

        if user_input is not None:
            host = user_input[CONF_HOST].strip().rstrip("/")
            api_key = (user_input.get(CONF_API_KEY) or "").strip() or entry.data[CONF_API_KEY]

            site_id, error = await self._async_validate(host, api_key)
            if error:
                errors["base"] = error
            elif site_id and entry.unique_id and site_id != entry.unique_id:
                # A different BEAAM answered — reconfiguring must not silently
                # repoint the entry at another device.
                errors["base"] = "wrong_device"
            else:
                self.hass.config_entries.async_update_entry(
                    entry,
                    title=f"neoom BEAAM ({host})",
                    data={**entry.data, CONF_HOST: host, CONF_API_KEY: api_key},
                )
                await self.hass.config_entries.async_reload(entry.entry_id)
                return self.async_abort(reason="reconfigure_successful")

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_HOST, default=entry.data[CONF_HOST]): str,
                    vol.Optional(CONF_API_KEY): str,
                }
            ),
            errors=errors,
        )

    # ------------------------------------------------------------------

    def _existing_entry(self) -> config_entries.ConfigEntry | None:
        """Return the entry this reauth/reconfigure flow belongs to.

        Newer Home Assistant releases offer dedicated helpers for this, but
        reading it from the flow context works across all supported versions.
        """
        entry_id = self.context.get("entry_id")
        if not entry_id:
            return None
        return self.hass.config_entries.async_get_entry(entry_id)

    @staticmethod
    def async_get_options_flow(config_entry: config_entries.ConfigEntry):
        return NeoomBeaamOptionsFlow(config_entry)


class NeoomBeaamOptionsFlow(config_entries.OptionsFlow):
    """Allow changing the scan interval after setup."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current_interval = self._config_entry.options.get(
            CONF_SCAN_INTERVAL,
            self._config_entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
        )

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_SCAN_INTERVAL, default=current_interval
                    ): SCAN_INTERVAL_SELECTOR,
                }
            ),
        )
