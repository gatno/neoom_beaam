"""Sensor platform for neoom BEAAM."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfFrequency,
    UnitOfPower,
    UnitOfReactivePower,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import BeaamApiClient
from .const import DOMAIN
from .coordinator import BeaamCoordinator
from .entity_base import BeaamBaseEntity, async_setup_dynamic_entities

_LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Descriptor for site-level sensors (from /site/state energyFlow)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class BeaamSiteSensorDescription(SensorEntityDescription):
    dp_key: str = ""
    # Some values use a sign convention where negative = "other direction"
    # abs_value: return abs(raw) always
    # positive_only: return raw if > 0 else 0
    # negative_as_positive: return raw*-1 if raw < 0 else raw
    value_transform: str = "raw"  # "raw" | "abs" | "positive_only" | "negative_as_positive"


SITE_SENSORS: tuple[BeaamSiteSensorDescription, ...] = (
    BeaamSiteSensorDescription(
        key="power_production",
        dp_key="POWER_PRODUCTION",
        name="PV Leistung",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:solar-panel",
        value_transform="positive_only",
    ),
    BeaamSiteSensorDescription(
        key="power_consumption_calc",
        dp_key="POWER_CONSUMPTION_CALC",
        name="Verbrauch",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:home-lightning-bolt-outline",
        value_transform="abs",
    ),
    BeaamSiteSensorDescription(
        key="power_grid_import",
        dp_key="POWER_GRID",
        name="Netzbezug",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:transmission-tower-import",
        value_transform="positive_only",
    ),
    BeaamSiteSensorDescription(
        key="power_grid_export",
        dp_key="POWER_GRID",
        name="Netzeinspeisung",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:transmission-tower-export",
        value_transform="negative_as_positive",
    ),
    BeaamSiteSensorDescription(
        key="power_storage_charge",
        dp_key="POWER_STORAGE",
        name="Batterie Laden",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:battery-arrow-up-outline",
        value_transform="negative_as_positive",
    ),
    BeaamSiteSensorDescription(
        key="power_storage_discharge",
        dp_key="POWER_STORAGE",
        name="Batterie Entladen",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:battery-arrow-down-outline",
        value_transform="positive_only",
    ),
    BeaamSiteSensorDescription(
        key="state_of_charge",
        dp_key="STATE_OF_CHARGE",
        name="Batterie Ladestand",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:battery-heart-variant",
    ),
    BeaamSiteSensorDescription(
        key="self_sufficiency",
        dp_key="SELF_SUFFICIENCY",
        name="Autarkiegrad",
        native_unit_of_measurement=PERCENTAGE,
        device_class=None,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:lightning-bolt-circle",
    ),
    BeaamSiteSensorDescription(
        key="energy_produced",
        dp_key="ENERGY_PRODUCED",
        name="Energie produziert",
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:solar-power-variant",
    ),
    BeaamSiteSensorDescription(
        key="energy_consumed_calc",
        dp_key="ENERGY_CONSUMED_CALC",
        name="Energie verbraucht",
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:home-lightning-bolt-outline",
        value_transform="abs",
    ),
    BeaamSiteSensorDescription(
        key="energy_imported",
        dp_key="ENERGY_IMPORTED",
        name="Energie importiert",
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:transmission-tower-import",
        value_transform="abs",
    ),
    BeaamSiteSensorDescription(
        key="energy_exported",
        dp_key="ENERGY_EXPORTED",
        name="Energie exportiert",
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:transmission-tower-export",
        value_transform="abs",
    ),
    BeaamSiteSensorDescription(
        key="energy_charged",
        dp_key="ENERGY_CHARGED",
        name="Energie geladen",
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:battery-arrow-up-outline",
    ),
    BeaamSiteSensorDescription(
        key="energy_discharged",
        dp_key="ENERGY_DISCHARGED",
        name="Energie entladen",
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:battery-arrow-down-outline",
    ),
    BeaamSiteSensorDescription(
        key="energy_consumed",
        dp_key="ENERGY_CONSUMED",
        name="Energie verbraucht (Messung)",
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:home-lightning-bolt-outline",
        value_transform="abs",
    ),
    # --- Energiefluss-Anteile (0–1 → ×100 = %) ---
    BeaamSiteSensorDescription(
        key="fraction_pv_to_consumption",
        dp_key="FRACTION_PV_TO_CONSUMPTION",
        name="PV → Verbrauch Anteil",
        native_unit_of_measurement=PERCENTAGE,
        device_class=None,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:solar-power-variant-outline",
        value_transform="fraction_to_percent",
    ),
    BeaamSiteSensorDescription(
        key="fraction_pv_to_storage",
        dp_key="FRACTION_PV_TO_STORAGE",
        name="PV → Speicher Anteil",
        native_unit_of_measurement=PERCENTAGE,
        device_class=None,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:battery-arrow-up-outline",
        value_transform="fraction_to_percent",
    ),
    BeaamSiteSensorDescription(
        key="fraction_pv_to_grid",
        dp_key="FRACTION_PV_TO_GRID",
        name="PV → Netz Anteil",
        native_unit_of_measurement=PERCENTAGE,
        device_class=None,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:solar-power-variant-outline",
        value_transform="fraction_to_percent",
    ),
    BeaamSiteSensorDescription(
        key="fraction_grid_to_consumption",
        dp_key="FRACTION_GRID_TO_CONSUMPTION",
        name="Netz → Verbrauch Anteil",
        native_unit_of_measurement=PERCENTAGE,
        device_class=None,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:transmission-tower",
        value_transform="fraction_to_percent",
    ),
    BeaamSiteSensorDescription(
        key="fraction_grid_to_storage",
        dp_key="FRACTION_GRID_TO_STORAGE",
        name="Netz → Speicher Anteil",
        native_unit_of_measurement=PERCENTAGE,
        device_class=None,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:battery-plus-outline",
        value_transform="fraction_to_percent",
    ),
    BeaamSiteSensorDescription(
        key="fraction_storage_to_consumption",
        dp_key="FRACTION_STORAGE_TO_CONSUMPTION",
        name="Speicher → Verbrauch Anteil",
        native_unit_of_measurement=PERCENTAGE,
        device_class=None,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:battery-outline",
        value_transform="fraction_to_percent",
    ),
    BeaamSiteSensorDescription(
        key="fraction_storage_to_grid",
        dp_key="FRACTION_STORAGE_TO_GRID",
        name="Speicher → Netz Anteil",
        native_unit_of_measurement=PERCENTAGE,
        device_class=None,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:battery-arrow-down-outline",
        value_transform="fraction_to_percent",
    ),
)


# ---------------------------------------------------------------------------
# Descriptor for per-Thing sensors (from /things/{id}/states)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class BeaamThingSensorDescription(SensorEntityDescription):
    dp_key: str = ""


THING_SENSORS: tuple[BeaamThingSensorDescription, ...] = (
    # ----- Spannung / Phasen -----
    BeaamThingSensorDescription(
        key="voltage_p1",
        dp_key="VOLTAGE_P1",
        name="Spannung L1",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    BeaamThingSensorDescription(
        key="voltage_p2",
        dp_key="VOLTAGE_P2",
        name="Spannung L2",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    BeaamThingSensorDescription(
        key="voltage_p3",
        dp_key="VOLTAGE_P3",
        name="Spannung L3",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    BeaamThingSensorDescription(
        key="frequency",
        dp_key="FREQUENCY",
        name="Frequenz",
        native_unit_of_measurement=UnitOfFrequency.HERTZ,
        device_class=SensorDeviceClass.FREQUENCY,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    # ----- Leistung -----
    BeaamThingSensorDescription(
        key="active_power",
        dp_key="ACTIVE_POWER",
        name="Wirkleistung",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    BeaamThingSensorDescription(
        key="reactive_power",
        dp_key="REACTIVE_POWER",
        name="Blindleistung",
        native_unit_of_measurement=UnitOfReactivePower.VOLT_AMPERE_REACTIVE,
        device_class=SensorDeviceClass.REACTIVE_POWER,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    BeaamThingSensorDescription(
        key="power",
        dp_key="POWER",
        name="Leistung",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    # Pro-Phase Leistung (Stromzähler)
    BeaamThingSensorDescription(
        key="power_p1",
        dp_key="POWER_P1",
        name="Leistung L1",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    BeaamThingSensorDescription(
        key="power_p2",
        dp_key="POWER_P2",
        name="Leistung L2",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    BeaamThingSensorDescription(
        key="power_p3",
        dp_key="POWER_P3",
        name="Leistung L3",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    # ----- Strom -----
    BeaamThingSensorDescription(
        key="current",
        dp_key="CURRENT",
        name="Strom",
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    # ----- Energie (Zähler) -----
    BeaamThingSensorDescription(
        key="produced_energy",
        dp_key="PRODUCED_ENERGY",
        name="Erzeugte Energie",
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:solar-power-variant",
    ),
    BeaamThingSensorDescription(
        key="charged_energy",
        dp_key="CHARGED_ENERGY",
        name="Geladene Energie",
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:battery-arrow-up-outline",
    ),
    BeaamThingSensorDescription(
        key="discharged_energy",
        dp_key="DISCHARGED_ENERGY",
        name="Entladene Energie",
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:battery-arrow-down-outline",
    ),
    BeaamThingSensorDescription(
        key="input_energy",
        dp_key="INPUT_ENERGY",
        name="Bezogene Energie",
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:transmission-tower-import",
    ),
    BeaamThingSensorDescription(
        key="output_energy",
        dp_key="OUTPUT_ENERGY",
        name="Eingespeiste Energie",
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:transmission-tower-export",
    ),
    # ----- Batterie-Zustand -----
    BeaamThingSensorDescription(
        key="state_of_charge",
        dp_key="STATE_OF_CHARGE",
        name="Ladezustand",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    BeaamThingSensorDescription(
        key="health_state",
        dp_key="HEALTH_STATE",
        name="Batterie Gesundheit",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:battery-heart-variant",
    ),
    BeaamThingSensorDescription(
        key="voltage",
        dp_key="VOLTAGE",
        name="Batteriespannung",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    # ----- Diagnose / Status -----
    BeaamThingSensorDescription(
        key="state_code",
        dp_key="STATE_CODE",
        name="Status Code",
        native_unit_of_measurement=None,
        device_class=None,
        state_class=None,
        icon="mdi:information-outline",
        entity_registry_enabled_default=False,
    ),
    BeaamThingSensorDescription(
        key="error_codes",
        dp_key="ERROR_CODES",
        name="Fehlercodes",
        native_unit_of_measurement=None,
        device_class=None,
        state_class=None,
        icon="mdi:alert-circle-outline",
        entity_registry_enabled_default=False,
    ),
)


def _apply_transform(raw: float | None, transform: str) -> float | None:
    if raw is None:
        return None
    if transform == "abs":
        return abs(raw)
    if transform == "positive_only":
        return max(0.0, raw)
    if transform == "negative_as_positive":
        return abs(raw) if raw < 0 else 0.0
    if transform == "fraction_to_percent":
        return round(raw * 100, 1)
    return raw  # "raw"


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

    def _build(known: set[str]) -> list[tuple[str, SensorEntity]]:
        new: list[tuple[str, SensorEntity]] = []

        for desc in SITE_SENSORS:
            key = f"site_{desc.key}"
            if key not in known:
                new.append((key, BeaamSiteSensor(coordinator, desc, site_device)))

        for thing in coordinator.things:
            thing_id = thing.get("id")

            # Only add sensors whose dp_key the BEAAM actually reports. A thing
            # that was offline at setup gets picked up on a later poll.
            thing_data = coordinator.data.get("things", {}).get(thing_id)
            if not thing_data:
                continue
            available_keys = {s.get("key") for s in thing_data.get("states", [])}

            thing_device = None
            for desc in THING_SENSORS:
                key = f"thing_{thing_id}_{desc.key}"
                if key in known or desc.dp_key not in available_keys:
                    continue
                if thing_device is None:
                    thing_device = coordinator.make_thing_device_info(thing_id, entry.entry_id)
                new.append((key, BeaamThingSensor(coordinator, desc, thing_id, thing_device)))

        return new

    async_setup_dynamic_entities(coordinator, entry, async_add_entities, _build)


# ---------------------------------------------------------------------------
# Sensor implementations
# ---------------------------------------------------------------------------

class BeaamSiteSensor(BeaamBaseEntity, SensorEntity):
    """Sensor for a site-level energy flow data point."""

    entity_description: BeaamSiteSensorDescription

    def __init__(
        self,
        coordinator: BeaamCoordinator,
        description: BeaamSiteSensorDescription,
        device_info: DeviceInfo,
    ) -> None:
        super().__init__(coordinator, f"site_{description.key}", device_info)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        site_state = self.coordinator.data.get("site_state", {})
        states = site_state.get("energyFlow", {}).get("states", [])
        raw = BeaamApiClient.extract_state_value(states, self.entity_description.dp_key)
        return _apply_transform(raw, self.entity_description.value_transform)


class BeaamThingSensor(BeaamBaseEntity, SensorEntity):
    """Sensor for a per-Thing data point."""

    entity_description: BeaamThingSensorDescription

    def __init__(
        self,
        coordinator: BeaamCoordinator,
        description: BeaamThingSensorDescription,
        thing_id: str,
        device_info: DeviceInfo,
    ) -> None:
        super().__init__(coordinator, f"thing_{thing_id}_{description.key}", device_info)
        self.entity_description = description
        self._thing_id = thing_id

    @property
    def native_value(self) -> Any:
        thing_data = self.coordinator.data.get("things", {}).get(self._thing_id)
        if not thing_data:
            return None
        states = thing_data.get("states", [])
        value = BeaamApiClient.extract_state_value(states, self.entity_description.dp_key)
        # ERROR_CODES is a list — join to a readable string
        if isinstance(value, list):
            return ", ".join(str(v) for v in value) if value else "OK"
        return value
