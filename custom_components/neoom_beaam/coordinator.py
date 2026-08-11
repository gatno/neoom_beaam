"""DataUpdateCoordinator for the neoom BEAAM integration."""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import BeaamApiClient, BeaamApiError
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

# The BEAAM is a small embedded device — don't hammer it with one request per
# thing at once, but don't poll them strictly sequentially either.
MAX_PARALLEL_REQUESTS = 4

# Tolerate this many consecutive failed polls before marking everything
# unavailable. A single hiccup should not blank out the whole dashboard.
MAX_FAILED_POLLS = 3


# How often to re-read /site/configuration, expressed in poll cycles. Picking up
# a newly added Thing should not require reloading the integration.
CONFIG_REFRESH_EVERY = 60


def extract_site_id(config: dict) -> str | None:
    """Return a stable identifier for the site from /site/configuration.

    Used as the config entry's unique_id so the entry survives an IP change.
    The BEAAM firmware is not consistent about where it puts this, so a few
    spellings are tried; None means "fall back to the host".
    """
    if not isinstance(config, dict):
        return None
    site_info = config.get("siteInfo") if isinstance(config.get("siteInfo"), dict) else {}
    for source in (site_info, config):
        for key in ("siteId", "id", "uuid", "serialNumber"):
            value = source.get(key)
            if isinstance(value, (str, int)) and str(value).strip():
                return str(value).strip()
    return None


def _merge_states(previous: Any, current: Any) -> list[dict]:
    """Overlay a fresh states list onto the previous snapshot.

    The BEAAM sometimes answers with only a subset of its data points — or with
    an explicit null value — while a sub-device reconnects. The response is
    valid JSON, so the request-level fallback below does not catch it and every
    affected sensor would flip to "unknown" until the device recovers. Keeping
    the last known value per data point makes those gaps invisible.
    """
    if not isinstance(current, list):
        return previous if isinstance(previous, list) else []

    merged: dict[str, dict] = {}
    if isinstance(previous, list):
        for item in previous:
            if isinstance(item, dict) and item.get("key"):
                merged[item["key"]] = item

    for item in current:
        if not isinstance(item, dict) or not item.get("key"):
            continue
        if item.get("value") is None and item["key"] in merged:
            # Data point present but empty — keep the last real reading.
            continue
        merged[item["key"]] = item

    return list(merged.values())


class BeaamCoordinator(DataUpdateCoordinator):
    """Fetches site state + per-thing states from the local BEAAM API."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: BeaamApiClient,
        scan_interval: int,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name="neoom BEAAM",
            update_interval=timedelta(seconds=scan_interval),
        )
        self.client = client
        # List of normalized thing dicts: {"id": str, "name": str, "thingType": str}
        # Populated in async_load_site_configuration().
        self._things: list[dict] = []
        self._site_coordinates: tuple[float, float] | None = None
        self._site_id: str | None = None
        self._semaphore = asyncio.Semaphore(MAX_PARALLEL_REQUESTS)
        self._failed_polls = 0
        self._polls_since_config_refresh = 0

    # ------------------------------------------------------------------
    # Initialisation: load site configuration to discover Things
    # ------------------------------------------------------------------

    # Human-readable German names for known thing types
    _TYPE_NAMES: dict[str, str] = {
        "PV": "PV",
        "INVERTER": "Wechselrichter",
        "BATTERY": "Batterie",
        "ELECTRICITY_METER_AC": "Stromzähler",
        "ELECTRICITY_METER_DC": "Gleichstromzähler",
        "CHARGING_POINT": "Ladepunkt",
        "HEAT_PUMP": "Wärmepumpe",
        "CLIENT_THING": "BEAAM Controller",
    }

    async def async_load_site_configuration(self) -> list[dict]:
        """Fetch /api/v1/site/configuration and build the things list.

        The BEAAM API returns things as a dict mapping UUID → {type, dataPoints, ...}.
        Named overrides are taken from siteInfo.gridConnections where available.
        Things of the same type are numbered (e.g. "PV 1", "PV 2").
        """
        config = await self.client.get_site_configuration()
        raw_things = config.get("things", {})
        self._site_id = extract_site_id(config)

        # Store geo coordinates for site DeviceInfo
        geo = config.get("siteInfo", {}).get("geoCoordinates", {})
        if geo.get("latitude") is not None and geo.get("longitude") is not None:
            self._site_coordinates = (float(geo["latitude"]), float(geo["longitude"]))

        # Build a name-override map from gridConnections (keyed by meterThingId)
        named_overrides: dict[str, str] = {}
        for gc in config.get("siteInfo", {}).get("gridConnections", {}).values():
            meter_id = gc.get("meterThingId")
            gc_name = gc.get("name")
            if meter_id and gc_name:
                named_overrides[meter_id] = gc_name

        # Count occurrences per type to decide whether to number them
        type_counts: dict[str, int] = {}
        if isinstance(raw_things, dict):
            for thing_data in raw_things.values():
                t = thing_data.get("type", "UNKNOWN")
                type_counts[t] = type_counts.get(t, 0) + 1
        elif isinstance(raw_things, list):
            for entry in raw_things:
                if isinstance(entry, dict):
                    t = entry.get("type", "UNKNOWN")
                    type_counts[t] = type_counts.get(t, 0) + 1

        # Assign running index per type for numbering
        type_index: dict[str, int] = {}

        normalized: list[dict] = []

        if isinstance(raw_things, dict):
            for thing_id, thing_data in raw_things.items():
                thing_type = thing_data.get("type", "UNKNOWN")

                if thing_id in named_overrides:
                    name = named_overrides[thing_id]
                else:
                    base = self._TYPE_NAMES.get(thing_type, thing_type)
                    if type_counts.get(thing_type, 1) > 1:
                        type_index[thing_type] = type_index.get(thing_type, 0) + 1
                        name = f"{base} {type_index[thing_type]}"
                    else:
                        name = base

                normalized.append({"id": thing_id, "name": name, "thingType": thing_type})

        elif isinstance(raw_things, list):
            # Fallback: legacy list format
            for entry in raw_things:
                if isinstance(entry, str):
                    normalized.append({"id": entry, "name": entry, "thingType": "UNKNOWN"})
                elif isinstance(entry, dict):
                    thing_id = entry.get("id", entry.get("thingId", ""))
                    thing_type = entry.get("type", entry.get("thingType", "UNKNOWN"))
                    if thing_id in named_overrides:
                        name = named_overrides[thing_id]
                    else:
                        base = self._TYPE_NAMES.get(thing_type, thing_type)
                        if type_counts.get(thing_type, 1) > 1:
                            type_index[thing_type] = type_index.get(thing_type, 0) + 1
                            name = f"{base} {type_index[thing_type]}"
                        else:
                            name = base
                    normalized.append({"id": thing_id, "name": name, "thingType": thing_type})
                else:
                    _LOGGER.warning("Unexpected thing entry type %s: %r", type(entry), entry)
        else:
            _LOGGER.warning("Unexpected 'things' format in site configuration: %r", type(raw_things))

        self._things = normalized
        _LOGGER.debug(
            "Discovered %d things: %s",
            len(self._things),
            [(t["id"], t["name"]) for t in self._things],
        )
        return self._things

    @property
    def things(self) -> list[dict]:
        return self._things

    @property
    def site_id(self) -> str | None:
        """Stable site identifier, or None if the firmware does not report one."""
        return self._site_id

    @property
    def site_coordinates(self) -> tuple[float, float] | None:
        """Return (latitude, longitude) from site configuration, or None."""
        return self._site_coordinates

    def make_thing_device_info(self, thing_id: str, entry_id: str) -> DeviceInfo:
        """Build a DeviceInfo for a Thing, enriched with serial/firmware from states."""
        thing = next((t for t in self._things if t["id"] == thing_id), None)
        thing_name = thing.get("name", thing_id) if thing else thing_id
        thing_type = thing.get("thingType", "UNKNOWN") if thing else "UNKNOWN"

        serial: str | None = None
        firmware: str | None = None
        if self.data:
            thing_data = self.data.get("things", {}).get(thing_id)
            if thing_data:
                states = thing_data.get("states", [])
                serial = BeaamApiClient.extract_state_value(states, "SERIAL_NUMBER")
                firmware = BeaamApiClient.extract_state_value(states, "FIRMWARE_VERSION")

        return DeviceInfo(
            identifiers={(DOMAIN, thing_id)},
            name=f"neoom {thing_name}",
            manufacturer="neoom",
            model=thing_type,
            via_device=(DOMAIN, entry_id),
            serial_number=serial or None,
            sw_version=firmware or None,
        )

    # ------------------------------------------------------------------
    # Regular poll
    # ------------------------------------------------------------------

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch site state and all per-thing states.

        Returns:
            {
                "site_state": <raw /site/state response>,
                "things": {
                    "<thingId>": <raw /things/{id}/states response>,
                    ...
                }
            }
        """
        previous = self.data or {}

        # Periodically re-read the site configuration so a Thing added to the
        # BEAAM shows up without reloading the integration. A failure here is
        # not fatal — the existing list stays in use.
        self._polls_since_config_refresh += 1
        if self._polls_since_config_refresh >= CONFIG_REFRESH_EVERY:
            self._polls_since_config_refresh = 0
            try:
                await self.async_load_site_configuration()
            except BeaamApiError as err:
                _LOGGER.debug("Could not refresh site configuration: %s", err)

        try:
            site_state = await self.client.get_site_state()
        except BeaamApiError as err:
            self._failed_polls += 1
            if self._failed_polls < MAX_FAILED_POLLS and previous:
                # Transient glitch — keep serving the last known values instead
                # of blanking every entity out.
                _LOGGER.warning(
                    "Site state poll %d/%d failed, keeping previous data: %s",
                    self._failed_polls, MAX_FAILED_POLLS, err,
                )
                return previous
            raise UpdateFailed(f"Error fetching site state: {err}") from err

        # Carry forward data points the BEAAM left out of this response.
        previous_site = previous.get("site_state") or {}
        if isinstance(site_state, dict):
            merged_flow = dict(site_state.get("energyFlow") or {})
            merged_flow["states"] = _merge_states(
                (previous_site.get("energyFlow") or {}).get("states"),
                merged_flow.get("states"),
            )
            site_state = {**site_state, "energyFlow": merged_flow}

        thing_ids = [t["id"] for t in self._things if t.get("id")]

        async def _fetch(thing_id: str) -> tuple[str, Any]:
            async with self._semaphore:
                try:
                    return thing_id, await self.client.get_thing_states(thing_id)
                except BeaamApiError as err:
                    _LOGGER.debug("Could not fetch states for thing %s: %s", thing_id, err)
                    return thing_id, None

        results = await asyncio.gather(*(_fetch(tid) for tid in thing_ids))

        previous_things = previous.get("things", {})
        things_data: dict[str, Any] = {}
        for thing_id, data in results:
            # Fall back to the last successful snapshot so a single missed
            # request does not push the thing's sensors to "unknown".
            if data is None:
                things_data[thing_id] = previous_things.get(thing_id)
                continue
            prev = previous_things.get(thing_id) or {}
            things_data[thing_id] = {
                **data,
                "states": _merge_states(prev.get("states"), data.get("states")),
            }

        self._failed_polls = 0

        return {
            "site_state": site_state,
            "things": things_data,
        }
