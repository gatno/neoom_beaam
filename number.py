"""Number platform for neoom BEAAM (writable states, e.g. emergency reserve)."""
from __future__ import annotations

import logging
from dataclasses import dataclass

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfElectricCurrent, UnitOfPower
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import BeaamApiClient
from .const import DOMAIN
from .coordinator import BeaamCoordinator
from .entity_base import BeaamBaseEntity, async_setup_dynamic_entities

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class BeaamNumberDescription(NumberEntityDescription):
    """Extended description for writable BEAAM number entities."""

    # DataPoint key used to READ the current value from /things/{id}/states.
    # Writes go back to the same endpoint via POST.
    dp_key: str = ""
    # The BEAAM stores the value with an 8 % system reserve baked in.
    # HA shows (raw - system_reserve) and sends (user_value + system_reserve) to the API.
    system_reserve: int = 0


BATTERY_NUMBERS: tuple[BeaamNumberDescription, ...] = (
    BeaamNumberDescription(
        key="min_soc_self_consumption",
        dp_key="MIN_SOC_SELF_CONSUMPTION_OPT",
        name="Min. SOC Eigenverbrauch",
        native_min_value=0,
        native_max_value=92,   # 100 - 8 % system reserve
        native_step=1,
        native_unit_of_measurement=PERCENTAGE,
        device_class=NumberDeviceClass.BATTERY,
        mode=NumberMode.SLIDER,
        icon="mdi:battery-sync-outline",
        system_reserve=8,
    ),
    BeaamNumberDescription(
        key="min_soc",
        dp_key="MIN_SOC",
        name="Min. SOC",
        native_min_value=0,
        native_max_value=92,
        native_step=1,
        native_unit_of_measurement=PERCENTAGE,
        device_class=NumberDeviceClass.BATTERY,
        mode=NumberMode.SLIDER,
        icon="mdi:battery-low",
        system_reserve=8,
    ),
    BeaamNumberDescription(
        key="min_soc_backup_energy",
        dp_key="MIN_SOC_BACKUP_ENERGY",
        name="Min. SOC Notstrom",
        native_min_value=0,
        native_max_value=92,
        native_step=1,
        native_unit_of_measurement=PERCENTAGE,
        device_class=NumberDeviceClass.BATTERY,
        mode=NumberMode.SLIDER,
        icon="mdi:battery-lock",
        system_reserve=8,
    ),
    BeaamNumberDescription(
        key="max_current_charge",
        dp_key="MAX_CURRENT_CHARGE",
        name="Max. Ladestrom",
        native_min_value=0,
        native_max_value=100,
        native_step=1,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        device_class=NumberDeviceClass.CURRENT,
        mode=NumberMode.BOX,
        icon="mdi:current-dc",
    ),
    BeaamNumberDescription(
        key="max_current_discharge",
        dp_key="MAX_CURRENT_DISCHARGE",
        name="Max. Entladestrom",
        native_min_value=0,
        native_max_value=100,
        native_step=1,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        device_class=NumberDeviceClass.CURRENT,
        mode=NumberMode.BOX,
        icon="mdi:current-dc",
    ),
    BeaamNumberDescription(
        key="target_power",
        dp_key="TARGET_POWER",
        name="Zielleistung",
        native_min_value=-30000,
        native_max_value=30000,
        native_step=100,
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=NumberDeviceClass.POWER,
        mode=NumberMode.BOX,
        icon="mdi:battery-arrow-up-outline",
    ),
)

INVERTER_NUMBERS: tuple[BeaamNumberDescription, ...] = (
    BeaamNumberDescription(
        key="active_power_limit",
        dp_key="ACTIVE_POWER_LIMIT",
        name="Wirkleistungsbegrenzung",
        native_min_value=0,
        native_max_value=100000,
        native_step=100,
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=NumberDeviceClass.POWER,
        mode=NumberMode.BOX,
        icon="mdi:solar-power",
    ),
    BeaamNumberDescription(
        key="max_power_grid_feed_in",
        dp_key="MAX_POWER_GRID_FEED_IN",
        name="Max. Einspeisung",
        native_min_value=0,
        native_max_value=100000,
        native_step=100,
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=NumberDeviceClass.POWER,
        mode=NumberMode.BOX,
        icon="mdi:transmission-tower-export",
    ),
)

# Map thing type → applicable number descriptions
_TYPE_TO_NUMBERS: dict[str, tuple[BeaamNumberDescription, ...]] = {
    "BATTERY": BATTERY_NUMBERS,
    "INVERTER": INVERTER_NUMBERS,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: BeaamCoordinator = hass.data[DOMAIN][entry.entry_id]

    def _build(known: set[str]) -> list[tuple[str, NumberEntity]]:
        new: list[tuple[str, NumberEntity]] = []

        for thing in coordinator.things:
            thing_id = thing.get("id")
            applicable = _TYPE_TO_NUMBERS.get(thing.get("thingType", ""))
            if not applicable:
                continue

            thing_data = coordinator.data.get("things", {}).get(thing_id)
            if not thing_data:
                continue
            available_keys = {s.get("key") for s in thing_data.get("states", [])}

            thing_device = None
            for desc in applicable:
                key = f"thing_{thing_id}_{desc.key}"
                if key in known or desc.dp_key not in available_keys:
                    continue
                if thing_device is None:
                    thing_device = coordinator.make_thing_device_info(thing_id, entry.entry_id)
                new.append((key, BeaamReserveNumber(coordinator, desc, thing_id, thing_device)))

        return new

    async_setup_dynamic_entities(coordinator, entry, async_add_entities, _build)


class BeaamReserveNumber(BeaamBaseEntity, NumberEntity):
    """Writable number for BEAAM battery settings (e.g. min SOC / emergency reserve)."""

    entity_description: BeaamNumberDescription

    def __init__(
        self,
        coordinator: BeaamCoordinator,
        description: BeaamNumberDescription,
        thing_id: str,
        device_info: DeviceInfo,
    ) -> None:
        super().__init__(coordinator, f"thing_{thing_id}_{description.key}", device_info)
        self.entity_description = description
        self._thing_id = thing_id

    @property
    def native_value(self) -> float | None:
        thing_data = self.coordinator.data.get("things", {}).get(self._thing_id)
        if not thing_data:
            return None
        states = thing_data.get("states", [])
        val = BeaamApiClient.extract_state_value(states, self.entity_description.dp_key)
        if val is None:
            return None
        # Subtract system reserve so the user sees the "real" target
        return max(0.0, float(val) - self.entity_description.system_reserve)

    async def async_set_native_value(self, value: float) -> None:
        """Write the new value to the BEAAM, adding the system reserve back."""
        api_value = int(value) + self.entity_description.system_reserve
        _LOGGER.debug(
            "Setting %s on thing %s: user=%s api=%s",
            self.entity_description.dp_key,
            self._thing_id,
            value,
            api_value,
        )
        await self.coordinator.client.set_thing_states(
            self._thing_id,
            [{"key": self.entity_description.dp_key, "value": api_value}],
        )
        await self.coordinator.async_request_refresh()
