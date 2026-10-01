"""Gemeinsame Basis für die Parameter-Entitäten."""

from __future__ import annotations

from typing import Any

from homeassistant.const import EntityCategory
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, WOLFLINK_DOMAIN


class WolfWriteEntity(CoordinatorEntity):
    """Entität für einen Wolf-Parameter aus der Expertenliste.

    Standardmäßig deaktiviert: die Expertenliste umfasst je nach Anlage
    mehrere hundert Parameter. Der Nutzer aktiviert gezielt, was er braucht.
    """

    _attr_has_entity_name = True
    _attr_entity_registry_enabled_default = False
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, manager: Any, target: Any, param: Any) -> None:
        # Nicht der Coordinator von wolflink: der liest nur die Benutzerebene.
        # Die Komponente ruft die Werte der angemeldeten Parameter selbst ab.
        super().__init__(manager.coordinator)
        self._manager = manager
        self._target = target
        self._param = param
        self._attr_unique_id = f"{DOMAIN}_{target.system_id}_{param.value_id}"
        # Bei mehrfach vorkommenden Namen hängt der Anzeigename die value_id an
        # — sonst heißen beide Heizkreise gleich und man rät.
        self._attr_name = target.display_name(param)
        self._attr_device_info = DeviceInfo(
            identifiers={(WOLFLINK_DOMAIN, str(target.system_id))}
        )

    async def async_added_to_hass(self) -> None:
        """Für den Werteabruf anmelden — nur aktivierte Entitäten landen hier."""
        await super().async_added_to_hass()
        self._manager.async_register_poll(self._target.system_id, self._param.value_id)
        # Gebündelt: der Coordinator entprellt mehrere Anfragen zu einem Abruf.
        await self.coordinator.async_request_refresh()

    async def async_will_remove_from_hass(self) -> None:
        self._manager.async_unregister_poll(self._target.system_id, self._param.value_id)
        await super().async_will_remove_from_hass()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "value_id": int(self._param.value_id),
            "parameter_id": int(self._param.parameter_id),
            "bundle_id": int(self._param.bundle_id),
            "bereich": self._param.parent,
        }

    @property
    def available(self) -> bool:
        return super().available and self._target.current_value(self._param) is not None
