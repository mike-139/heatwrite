"""Knöpfe für Rückgängig und Snapshot — ohne den Umweg über die Services."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import WolfWriteConfigEntry, WolfWriteManager
from .const import DOMAIN, RESULT_OK, SIGNAL_UPDATED

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: WolfWriteConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    manager = entry.runtime_data
    async_add_entities(
        [WolfWriteUndoButton(manager, entry), WolfWriteSnapshotButton(manager, entry)]
    )


class WolfWriteBaseButton(ButtonEntity):
    """Basis für die Knöpfe am Dienst-Gerät."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, manager: WolfWriteManager, entry: WolfWriteConfigEntry) -> None:
        self._manager = manager
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="HeatWrite",
            manufacturer="HeatWrite",
            model="Schreibzugriff über Wolf Smartset",
            entry_type=DeviceEntryType.SERVICE,
        )

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(self.hass, SIGNAL_UPDATED, self._handle_update)
        )

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()


class WolfWriteUndoButton(WolfWriteBaseButton):
    """Nimmt die letzte erfolgreiche Änderung zurück."""

    _attr_name = "Letzte Änderung rückgängig"
    _attr_icon = "mdi:undo-variant"

    def __init__(self, manager: WolfWriteManager, entry: WolfWriteConfigEntry) -> None:
        super().__init__(manager, entry)
        self._attr_unique_id = f"{DOMAIN}_{entry.entry_id}_undo"

    @property
    def available(self) -> bool:
        """Nur drückbar, wenn es überhaupt etwas zurückzunehmen gibt."""
        return any(
            record.get("ergebnis") == RESULT_OK
            and not record.get("rueckgaengig")
            and record.get("alt") is not None
            for record in self._manager.history
        )

    async def async_press(self) -> None:
        results = await self._manager.async_undo(count=1)
        for result in results:
            _LOGGER.info(
                "Rückgängig: '%s' zurück auf %s",
                result["parameter"],
                result["neu"],
            )


class WolfWriteSnapshotButton(WolfWriteBaseButton):
    """Legt einen Snapshot aller beschreibbaren Parameter an."""

    _attr_name = "Snapshot anlegen"
    _attr_icon = "mdi:content-save-outline"

    def __init__(self, manager: WolfWriteManager, entry: WolfWriteConfigEntry) -> None:
        super().__init__(manager, entry)
        self._attr_unique_id = f"{DOMAIN}_{entry.entry_id}_snapshot"

    async def async_press(self) -> None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        await self._manager.async_snapshot_create(f"manuell-{stamp}")
