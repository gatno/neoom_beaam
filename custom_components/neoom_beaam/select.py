"""Select platform for neoom BEAAM (controllable string/enum states)."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import BeaamApiClient, BeaamApiError
from .const import DOMAIN
from .coordinator import BeaamCoordinator
from .entity_base import BeaamBaseEntity, async_setup_dynamic_entities

_LOGGER = logging.getLogger(__name__)

# How many polls an optimistically shown option survives before the device's own
# reading takes over again.
PENDING_MAX_POLLS = 6


@dataclass(frozen=True)
class BeaamSelectDescription(SelectEntityDescription):
    """Description for a BEAAM select entity."""

    # DataPoint key used to READ the current value from /things/{id}/states.
    # Writes go back to the same endpoint via POST.
    dp_key: str = ""
    # Available options — may need adjustment if firmware exposes different values
    options: list[str] = field(default_factory=list)


# Battery operating modes
_BATTERY_OPERATING_MODES = [
    "SELF_CONSUMPTION_OPT",   # Eigenverbrauchsoptimierung
    "BACKUP_POWER",           # Notstromversorgung
    "MANUAL",                 # Manueller Modus
    "TIME_OF_USE",            # Zeitbasiertes Laden
]

# Battery controller modes
_BATTERY_CONTROLLER_MODES = [
    "AUTO",
    "MANUAL",
]

# CLIENT_THING (BEAAM Controller) operating modes
_CLIENT_OPERATING_MODES = [
    "AUTO",
    "MANUAL",
]


THING_SELECTS: tuple[BeaamSelectDescription, ...] = (
    BeaamSelectDescription(
        key="battery_operating_mode",
        dp_key="OPERATING_MODE",
        name="Betriebsmodus",
        icon="mdi:battery-settings-outline",
        options=_BATTERY_OPERATING_MODES,
    ),
    BeaamSelectDescription(
        key="battery_controller_mode",
        dp_key="CONTROLLER_MODE",
        name="Controller Modus",
        icon="mdi:cog-outline",
        options=_BATTERY_CONTROLLER_MODES,
    ),
    BeaamSelectDescription(
        key="client_operating_mode",
        dp_key="OPERATING_MODE",
        name="Betriebsmodus",
        icon="mdi:tune",
        options=_CLIENT_OPERATING_MODES,
    ),
)

# Map thing type → which select descriptions apply
_TYPE_TO_SELECTS: dict[str, list[str]] = {
    "BATTERY": ["battery_operating_mode", "battery_controller_mode"],
    "CLIENT_THING": ["client_operating_mode"],
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: BeaamCoordinator = hass.data[DOMAIN][entry.entry_id]
    selects_by_key = {d.key: d for d in THING_SELECTS}

    def _build(known: set[str]) -> list[tuple[str, SelectEntity]]:
        new: list[tuple[str, SelectEntity]] = []

        for thing in coordinator.things:
            thing_id = thing.get("id")
            applicable_keys = _TYPE_TO_SELECTS.get(thing.get("thingType", ""), [])
            if not applicable_keys:
                continue

            thing_data = coordinator.data.get("things", {}).get(thing_id)
            if not thing_data:
                continue
            available_keys = {s.get("key") for s in thing_data.get("states", [])}

            thing_device = None
            for select_key in applicable_keys:
                desc = selects_by_key[select_key]
                key = f"thing_{thing_id}_{desc.key}"
                if key in known or desc.dp_key not in available_keys:
                    continue
                if thing_device is None:
                    thing_device = coordinator.make_thing_device_info(thing_id, entry.entry_id)
                new.append((key, BeaamSelectEntity(coordinator, desc, thing_id, thing_device)))

        return new

    async_setup_dynamic_entities(coordinator, entry, async_add_entities, _build)


class BeaamSelectEntity(BeaamBaseEntity, SelectEntity):
    """Select entity for a controllable BEAAM enum state."""

    entity_description: BeaamSelectDescription

    def __init__(
        self,
        coordinator: BeaamCoordinator,
        description: BeaamSelectDescription,
        thing_id: str,
        device_info: DeviceInfo,
    ) -> None:
        super().__init__(coordinator, f"thing_{thing_id}_{description.key}", device_info)
        self.entity_description = description
        self._thing_id = thing_id
        self._attr_options = description.options
        # Option written but not yet confirmed by the device.
        self._pending_option: str | None = None
        self._pending_polls = 0

    def _reported_option(self) -> str | None:
        thing_data = self.coordinator.data.get("things", {}).get(self._thing_id)
        if not thing_data:
            return None
        states = thing_data.get("states", [])
        value = BeaamApiClient.extract_state_value(states, self.entity_description.dp_key)
        if value is None:
            return None
        # If the API returns a value not in our known options list, add it dynamically
        if value not in self._attr_options:
            _LOGGER.debug(
                "Unknown option '%s' for %s on thing %s — adding to options list",
                value,
                self.entity_description.dp_key,
                self._thing_id,
            )
            self._attr_options = [*self._attr_options, value]
        return value

    @property
    def current_option(self) -> str | None:
        if self._pending_option is not None:
            return self._pending_option
        return self._reported_option()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Drop the optimistic option once the device confirms it."""
        if self._pending_option is not None:
            self._pending_polls += 1
            if (
                self._reported_option() == self._pending_option
                or self._pending_polls >= PENDING_MAX_POLLS
            ):
                self._pending_option = None
                self._pending_polls = 0
        super()._handle_coordinator_update()

    async def async_select_option(self, option: str) -> None:
        """Send the selected option to the BEAAM."""
        _LOGGER.debug(
            "Setting %s on thing %s to '%s'",
            self.entity_description.dp_key,
            self._thing_id,
            option,
        )
        try:
            await self.coordinator.client.set_thing_states(
                self._thing_id,
                [{"key": self.entity_description.dp_key, "value": option}],
            )
        except BeaamApiError as err:
            raise HomeAssistantError(
                f"Konnte {self.entity_description.dp_key} nicht auf '{option}' setzen: {err}"
            ) from err

        # Show the new option straight away instead of waiting for the next poll.
        self._pending_option = option
        self._pending_polls = 0
        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()
