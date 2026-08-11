"""Base entity for neoom BEAAM."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .coordinator import BeaamCoordinator


class BeaamBaseEntity(CoordinatorEntity[BeaamCoordinator]):
    """Base class for all neoom BEAAM entities."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: BeaamCoordinator,
        unique_id_suffix: str,
        device_info: DeviceInfo,
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_{unique_id_suffix}"
        self._attr_device_info = device_info


def async_setup_dynamic_entities(
    coordinator: BeaamCoordinator,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
    builder: Callable[[set[str]], list[tuple[str, Any]]],
) -> None:
    """Add entities now and again whenever new data points show up.

    A Thing that was offline during setup — or a data point the BEAAM only
    starts reporting later — would otherwise never get an entity until the
    integration is reloaded. `builder` receives the set of keys already added
    and returns (key, entity) pairs for everything new.
    """
    known: set[str] = set()

    @callback
    def _add_new() -> None:
        new = builder(known)
        if not new:
            return
        known.update(key for key, _ in new)
        async_add_entities([entity for _, entity in new])

    _add_new()
    entry.async_on_unload(coordinator.async_add_listener(_add_new))
