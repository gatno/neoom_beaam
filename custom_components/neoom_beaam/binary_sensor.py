"""Binary sensor platform for neoom BEAAM."""
from __future__ import annotations

import logging
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import BeaamApiClient
from .const import DOMAIN
from .coordinator import BeaamCoordinator
from .entity_base import BeaamBaseEntity, async_setup_dynamic_entities

_LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Site-level binary sensors (from /site/state energyFlow)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class BeaamSiteBinarySensorDescription(BinarySensorEntityDescription):
    dp_key: str = ""


SITE_BINARY_SENSORS: tuple[BeaamSiteBinarySensorDescription, ...] = (
    BeaamSiteBinarySensorDescription(
        key="producers_online",
        dp_key="PRODUCERS_ONLINE",
        name="Erzeuger online",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        icon="mdi:solar-panel",
    ),
    BeaamSiteBinarySensorDescription(
        key="storages_online",
        dp_key="STORAGES_ONLINE",
        name="Speicher online",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        icon="mdi:battery-outline",
    ),
    BeaamSiteBinarySensorDescription(
        key="grid_meters_online",
        dp_key="GRID_METERS_ONLINE",
        name="Stromzähler online",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        icon="mdi:meter-electric-outline",
    ),
    BeaamSiteBinarySensorDescription(
        key="charging_points_online",
        dp_key="CHARGING_POINTS_ONLINE",
        name="Ladepunkte online",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        icon="mdi:ev-station",
    ),
    BeaamSiteBinarySensorDescription(
        key="heating_online",
        dp_key="HEATING_ONLINE",
        name="Heizung online",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        icon="mdi:heat-pump-outline",
    ),
)


# ---------------------------------------------------------------------------
# Per-Thing binary sensors (from /things/{id}/states)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class BeaamThingBinarySensorDescription(BinarySensorEntityDescription):
    dp_key: str = ""


THING_BINARY_SENSORS: tuple[BeaamThingBinarySensorDescription, ...] = (
    BeaamThingBinarySensorDescription(
        key="connection",
        dp_key="CONNECTION",
        name="Verbunden",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    BeaamThingBinarySensorDescription(
        key="eps_mode_active",
        dp_key="EPS_MODE_ACTIVE",
        name="Notstrom aktiv",
        device_class=BinarySensorDeviceClass.POWER,
        icon="mdi:home-lightning-bolt",
    ),
)


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: BeaamCoordinator = hass.data[DOMAIN][entry.entry_id]

    site_device = DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name="neoom BEAAM",
        manufacturer="neoom",
        model="BEAAM",
        configuration_url=f"http://{entry.data['host']}/api/",
    )

    def _build(known: set[str]) -> list[tuple[str, BinarySensorEntity]]:
        new: list[tuple[str, BinarySensorEntity]] = []

        for desc in SITE_BINARY_SENSORS:
            key = f"site_{desc.key}"
            if key not in known:
                new.append((key, BeaamSiteBinarySensor(coordinator, desc, site_device)))

        for thing in coordinator.things:
            thing_id = thing.get("id")

            thing_data = coordinator.data.get("things", {}).get(thing_id)
            if not thing_data:
                continue
            available_keys = {s.get("key") for s in thing_data.get("states", [])}

            thing_device = None
            for desc in THING_BINARY_SENSORS:
                key = f"thing_{thing_id}_{desc.key}"
                if key in known or desc.dp_key not in available_keys:
                    continue
                if thing_device is None:
                    thing_device = coordinator.make_thing_device_info(thing_id, entry.entry_id)
                new.append(
                    (key, BeaamThingBinarySensor(coordinator, desc, thing_id, thing_device))
                )

        return new

    async_setup_dynamic_entities(coordinator, entry, async_add_entities, _build)


# ---------------------------------------------------------------------------
# Entity implementations
# ---------------------------------------------------------------------------

class BeaamSiteBinarySensor(BeaamBaseEntity, BinarySensorEntity):
    """Binary sensor for a site-level boolean data point."""

    entity_description: BeaamSiteBinarySensorDescription

    def __init__(
        self,
        coordinator: BeaamCoordinator,
        description: BeaamSiteBinarySensorDescription,
        device_info: DeviceInfo,
    ) -> None:
        super().__init__(coordinator, f"site_{description.key}", device_info)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        site_state = self.coordinator.data.get("site_state", {})
        states = site_state.get("energyFlow", {}).get("states", [])
        value = BeaamApiClient.extract_state_value(states, self.entity_description.dp_key)
        if value is None:
            return None
        return bool(value)


class BeaamThingBinarySensor(BeaamBaseEntity, BinarySensorEntity):
    """Binary sensor for a per-Thing boolean data point."""

    entity_description: BeaamThingBinarySensorDescription

    def __init__(
        self,
        coordinator: BeaamCoordinator,
        description: BeaamThingBinarySensorDescription,
        thing_id: str,
        device_info: DeviceInfo,
    ) -> None:
        super().__init__(coordinator, f"thing_{thing_id}_{description.key}", device_info)
        self.entity_description = description
        self._thing_id = thing_id

    @property
    def is_on(self) -> bool | None:
        thing_data = self.coordinator.data.get("things", {}).get(self._thing_id)
        if not thing_data:
            return None
        states = thing_data.get("states", [])
        value = BeaamApiClient.extract_state_value(states, self.entity_description.dp_key)
        if value is None:
            return None
        return bool(value)
