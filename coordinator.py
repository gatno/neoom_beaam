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
        self._semaphore = asyncio.Semaphore(MAX_PARALLEL_REQUESTS)
        self._failed_polls = 0

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

        try:
            site_state = await self.client.get_site_state()
        except BeaamApiError as err:
            self._failed_polls += 1
            if self._failed_polls < MAX_FAILED_POLLS and previous:
                # Transient glitch — keep serving the last known values instead
                # of blanking every entity out.
                _LOGGER.debug(
                    "Site state poll %d/%d failed, keeping previous data: %s",
                    self._failed_polls, MAX_FAILED_POLLS, err,
                )
                return previous
            raise UpdateFailed(f"Error fetching site state: {err}") from err

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
            things_data[thing_id] = data if data is not None else previous_things.get(thing_id)

        self._failed_polls = 0

        return {
            "site_state": site_state,
            "things": things_data,
        }
