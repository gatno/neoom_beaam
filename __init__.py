"""The neoom BEAAM integration."""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import BeaamApiClient, BeaamApiError, BeaamAuthError
from .const import CONF_API_KEY, CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL, DOMAIN
from .coordinator import BeaamCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.SENSOR, Platform.BINARY_SENSOR, Platform.NUMBER, Platform.SELECT]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up neoom BEAAM from a config entry."""
    host = entry.data[CONF_HOST]
    api_key = entry.data[CONF_API_KEY]
    scan_interval = entry.options.get(
        CONF_SCAN_INTERVAL, entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
    )

    session = async_get_clientsession(hass)
    client = BeaamApiClient(host=host, api_key=api_key, session=session)

    coordinator = BeaamCoordinator(hass, client, scan_interval)

    # Discover all Things before forwarding to platforms. If the BEAAM is not
    # reachable yet (both devices booting at the same time), tell HA to retry
    # instead of failing the entry until the next restart.
    try:
        await coordinator.async_load_site_configuration()
    except BeaamAuthError as err:
        raise ConfigEntryAuthFailed(str(err)) from err
    except BeaamApiError as err:
        raise ConfigEntryNotReady(f"BEAAM at {host} not reachable: {err}") from err

    # Initial data fetch
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options update (e.g. changed scan interval)."""
    await hass.config_entries.async_reload(entry.entry_id)
