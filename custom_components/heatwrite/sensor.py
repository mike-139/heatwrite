"""Sensoren: schreibgeschützte Parameter, Änderungshistorie und Snapshots."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.const import EntityCategory
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import WolfWriteConfigEntry, WolfWriteManager
from .const import DOMAIN, SIGNAL_UPDATED
from .entity import WolfWriteEntity

_LOGGER = logging.getLogger(__name__)

# Wie viele Historieneinträge als Attribut mitgegeben werden. Der komplette
# Verlauf liegt in .storage; die Attribute landen im Recorder und sollen den
# nicht aufblähen.
ATTRIBUTE_ENTRIES = 20


async def async_setup_entry(
    hass: HomeAssistant,
    entry: WolfWriteConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    manager = entry.runtime_data
    entities: list[SensorEntity] = [
        WolfWriteHistorySensor(manager, entry),
        WolfWriteSnapshotSensor(manager, entry),
    ]
    # Schreibgeschützte Parameter der Expertenliste nur lesbar anbieten.
    # wolflink deckt davon die Benutzerebene ab, die Fachmann-Ebene nicht.
    for target in manager.targets.values():
        writable = target.writable
        for value_id, param in target.parameters.items():
            if value_id in writable:
                continue
            entities.append(WolfWriteParameterSensor(manager, target, param))
    async_add_entities(entities)


class WolfWriteParameterSensor(WolfWriteEntity, SensorEntity):
    """Ein schreibgeschützter Parameter aus der Expertenliste, nur lesend.

    Ohne Kategorie: Home Assistant verbietet sensor-Entitäten die Kategorie
    CONFIG, und „Diagnose" wäre irreführend — ein Sollwert ist keine
    Diagnoseinformation. Also steht er bei den normalen Sensoren.
    """

    _attr_entity_category = None

    def __init__(self, manager, target, param) -> None:
        super().__init__(manager, target, param)
        self._attr_native_unit_of_measurement = getattr(param, "unit", None)

    @property
    def native_value(self) -> Any:
        raw = self._target.current_value(self._param)
        if raw is None:
            return None
        text = str(raw)

        # Auswahlparameter: die Rohzahl in den Anzeigenamen übersetzen.
        for item in getattr(self._param, "items", None) or []:
            if str(item.value) == text or item.name == text:
                return item.name

        if self._attr_native_unit_of_measurement is None:
            return text
        try:
            return float(text.replace(",", "."))
        except (TypeError, ValueError):
            # Die Regelung liefert für nicht belegte Werte Platzhalter wie '--'.
            return None


class WolfWriteBaseSensor(SensorEntity):
    """Basis für die beiden Diagnose-Sensoren."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_entity_category = EntityCategory.DIAGNOSTIC

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


class WolfWriteHistorySensor(WolfWriteBaseSensor):
    """Zeigt die letzte Änderung; der Verlauf hängt als Attribut daran."""

    _attr_name = "Letzte Änderung"
    _attr_icon = "mdi:history"

    def __init__(self, manager: WolfWriteManager, entry: WolfWriteConfigEntry) -> None:
        super().__init__(manager, entry)
        self._attr_unique_id = f"{DOMAIN}_{entry.entry_id}_history"

    @property
    def native_value(self) -> str | None:
        if not self._manager.history:
            return "keine"
        last = self._manager.history[-1]
        return f"{last['parameter']}: {last['alt']} → {last['neu']}"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        history = self._manager.history
        return {
            "anzahl_gesamt": len(history),
            "zeitpunkt": history[-1]["zeit"] if history else None,
            "rueckgaengig_moeglich": any(
                e.get("ergebnis") == "ok"
                and not e.get("rueckgaengig")
                and e.get("alt") is not None
                for e in history
            ),
            "verlauf": list(reversed(history[-ATTRIBUTE_ENTRIES:])),
        }


class WolfWriteSnapshotSensor(WolfWriteBaseSensor):
    """Zeigt die Anzahl gespeicherter Snapshots."""

    _attr_name = "Snapshots"
    _attr_icon = "mdi:backup-restore"
    _attr_native_unit_of_measurement = "Snapshots"

    def __init__(self, manager: WolfWriteManager, entry: WolfWriteConfigEntry) -> None:
        super().__init__(manager, entry)
        self._attr_unique_id = f"{DOMAIN}_{entry.entry_id}_snapshots"

    @property
    def native_value(self) -> int:
        return len(self._manager.snapshots)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "snapshots": {
                name: {"zeit": data["zeit"], "anzahl": data["anzahl"]}
                for name, data in sorted(self._manager.snapshots.items())
            }
        }
