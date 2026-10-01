"""Auswahlparameter der Wolf-Regelung als select-Entitäten."""

from __future__ import annotations

import logging

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import WolfWriteConfigEntry
from .const import SOURCE_ENTITY
from .entity import WolfWriteEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: WolfWriteConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    manager = entry.runtime_data
    entities: list[WolfWriteSelect] = []
    for target in manager.targets.values():
        for param in target.writable.values():
            if not hasattr(param, "items"):
                continue
            if not param.items:
                continue
            entities.append(WolfWriteSelect(manager, target, param))
    async_add_entities(entities)


class WolfWriteSelect(WolfWriteEntity, SelectEntity):
    """Ein beschreibbarer Auswahlparameter (ListItemParameter)."""

    def __init__(self, manager, target, param) -> None:
        super().__init__(manager, target, param)
        self._attr_options = [item.name for item in param.items]

    @property
    def current_option(self) -> str | None:
        raw = self._target.current_value(self._param)
        if raw is None:
            return None
        text = str(raw)
        for item in self._param.items:
            if item.name == text:
                return item.name
        # Die Leseseite liefert den Anzeigenamen, gelegentlich abweichend
        # geschrieben; zusätzlich gegen die Rohzahl prüfen.
        for item in self._param.items:
            if item.name.casefold() == text.casefold() or str(item.value) == text:
                return item.name
        _LOGGER.debug(
            "Unbekannte Option '%s' für '%s' — Optionen: %s",
            text,
            self._param.name,
            self._attr_options,
        )
        return None

    async def async_select_option(self, option: str) -> None:
        for item in self._param.items:
            if item.name == option:
                await self._manager.async_write(
                    self._target, self._param, item.value, source=SOURCE_ENTITY
                )
                return
        raise HomeAssistantError(
            f"'{option}' ist keine gültige Option für '{self._param.name}'."
        )
