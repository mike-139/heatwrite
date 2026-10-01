"""Einrichtung über die Oberfläche — ohne Eingaben."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers import config_validation as cv

from .const import (
    CONF_HISTORY_LIMIT,
    CONF_MAX_WRITES_PER_HOUR,
    CONF_SCAN_INTERVAL,
    DEFAULT_HISTORY_LIMIT,
    DEFAULT_MAX_WRITES_PER_HOUR,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    WOLFLINK_DOMAIN,
)


class WolfWriteConfigFlow(ConfigFlow, domain=DOMAIN):
    """Prüft nur, ob wolflink läuft — Zugangsdaten kommen von dort."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()

        entries = self.hass.config_entries.async_entries(WOLFLINK_DOMAIN)
        if not entries:
            return self.async_abort(reason="wolflink_missing")
        if not any(getattr(entry, "runtime_data", None) for entry in entries):
            return self.async_abort(reason="wolflink_not_loaded")

        if user_input is None:
            return self.async_show_form(step_id="user", data_schema=vol.Schema({}))

        return self.async_create_entry(title="HeatWrite", data={})

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return WolfWriteOptionsFlow()


class WolfWriteOptionsFlow(OptionsFlow):
    """Historienlänge und Ratenbegrenzung einstellen."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        options = self.config_entry.options
        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_HISTORY_LIMIT,
                    default=options.get(CONF_HISTORY_LIMIT, DEFAULT_HISTORY_LIMIT),
                ): vol.All(cv.positive_int, vol.Range(min=10, max=5000)),
                vol.Optional(
                    CONF_MAX_WRITES_PER_HOUR,
                    default=options.get(
                        CONF_MAX_WRITES_PER_HOUR, DEFAULT_MAX_WRITES_PER_HOUR
                    ),
                ): vol.All(cv.positive_int, vol.Range(min=1, max=500)),
                vol.Optional(
                    CONF_SCAN_INTERVAL,
                    default=options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                ): vol.All(cv.positive_int, vol.Range(min=1, max=120)),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
