"""Zahlenparameter der Wolf-Regelung als number-Entitäten."""

from __future__ import annotations

import logging

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import WolfWriteConfigEntry
from .const import DEFAULT_RANGE, SOURCE_ENTITY, UNIT_RANGES
from .entity import WolfWriteEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: WolfWriteConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    manager = entry.runtime_data
    entities: list[WolfWriteNumber] = []
    for target in manager.targets.values():
        for param in target.writable.values():
            if hasattr(param, "items"):
                continue  # Auswahlparameter → select.py
            entities.append(WolfWriteNumber(manager, target, param))
    async_add_entities(entities)


class WolfWriteNumber(WolfWriteEntity, NumberEntity):
    """Ein beschreibbarer Zahlenparameter."""

    _attr_mode = NumberMode.BOX

    def __init__(self, manager, target, param) -> None:
        super().__init__(manager, target, param)
        unit = getattr(param, "unit", None)
        minimum, maximum, step = UNIT_RANGES.get(unit, DEFAULT_RANGE)
        # Die Rohbeschreibung der Wolf-API nennt Min, Max und Schrittweite je
        # Parameter. Nur wo sie fehlen, greifen die pauschalen Bereiche.
        raw_min, raw_max, raw_step = target.limits(param)
        self._attr_native_unit_of_measurement = unit
        self._attr_native_min_value = minimum if raw_min is None else raw_min
        self._attr_native_max_value = maximum if raw_max is None else raw_max
        self._attr_native_step = step if not raw_step else raw_step

    @property
    def native_value(self) -> float | None:
        raw = self._target.current_value(self._param)
        if raw is None:
            return None
        try:
            return float(str(raw).replace(",", "."))
        except (TypeError, ValueError):
            return None

    async def async_set_native_value(self, value: float) -> None:
        # Ganzzahlige Werte ohne Nachkommastelle senden — die Wolf-Oberfläche
        # zeigt sie ebenso an, und manche Parameter sind reine Ganzzahlen.
        payload = int(value) if float(value).is_integer() else value
        await self._manager.async_write(
            self._target, self._param, payload, source=SOURCE_ENTITY
        )
