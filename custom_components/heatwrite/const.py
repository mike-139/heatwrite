"""Konstanten für HeatWrite."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "heatwrite"
WOLFLINK_DOMAIN: Final = "wolflink"

STORAGE_KEY: Final = f"{DOMAIN}.data"
STORAGE_VERSION: Final = 1

SIGNAL_UPDATED: Final = f"{DOMAIN}_updated"

# --- Optionen -------------------------------------------------------------
CONF_HISTORY_LIMIT: Final = "history_limit"
CONF_MAX_WRITES_PER_HOUR: Final = "max_writes_per_hour"
CONF_SCAN_INTERVAL: Final = "scan_interval"

DEFAULT_HISTORY_LIMIT: Final = 200
DEFAULT_MAX_WRITES_PER_HOUR: Final = 20
# Minuten zwischen zwei Werteabrufen. Einstellparameter ändern sich selten,
# und jeder Abruf geht über die inoffizielle Wolf-Cloud — deshalb gemächlich.
DEFAULT_SCAN_INTERVAL: Final = 5

# --- Services -------------------------------------------------------------
SERVICE_SET_VALUE: Final = "set_value"
SERVICE_SET_BY_NAME: Final = "set_by_name"
SERVICE_UNDO: Final = "undo"
SERVICE_SNAPSHOT_CREATE: Final = "snapshot_create"
SERVICE_SNAPSHOT_RESTORE: Final = "snapshot_restore"
SERVICE_SNAPSHOT_DELETE: Final = "snapshot_delete"
SERVICE_RELOAD_PARAMETERS: Final = "reload_parameters"
SERVICE_DESCRIBE_PARAMETER: Final = "describe_parameter"

# --- Service-Felder -------------------------------------------------------
ATTR_VALUE_ID: Final = "value_id"
ATTR_VALUE_IDS: Final = "value_ids"
ATTR_NAME: Final = "name"
ATTR_VALUE: Final = "value"
ATTR_SYSTEM_ID: Final = "system_id"
ATTR_DRY_RUN: Final = "dry_run"
ATTR_FORCE: Final = "force"
ATTR_COUNT: Final = "count"
ATTR_SNAPSHOT: Final = "snapshot"

# --- Ergebnis-Kennungen ---------------------------------------------------
RESULT_OK: Final = "ok"
RESULT_UNCHANGED: Final = "unchanged"
RESULT_DRY_RUN: Final = "dry_run"
RESULT_ERROR: Final = "error"

SOURCE_SERVICE: Final = "service"
SOURCE_ENTITY: Final = "entity"
SOURCE_UNDO: Final = "undo"
SOURCE_RESTORE: Final = "restore"

# Großzügige Wertebereiche je Einheit — die Wolf-API liefert kein Min/Max mit.
# Lieber weit gefasst als eine sinnvolle Einstellung blockieren; die Regelung
# lehnt unplausible Werte selbst ab.
UNIT_RANGES: Final[dict[str, tuple[float, float, float]]] = {
    # Einheit: (min, max, step)
    "°C": (-50.0, 100.0, 0.5),
    "bar": (0.0, 10.0, 0.1),
    "%": (0.0, 100.0, 1.0),
    "h": (0.0, 100000.0, 1.0),
    "kW": (0.0, 100.0, 0.1),
    "kWh": (0.0, 1000000.0, 1.0),
    "Wh": (0.0, 1000000.0, 1.0),
    "l/min": (0.0, 200.0, 0.1),
    "Hz": (0.0, 200.0, 1.0),
    "U/min": (0.0, 10000.0, 1.0),
    "rpm": (0.0, 10000.0, 1.0),
}

DEFAULT_RANGE: Final[tuple[float, float, float]] = (-10000.0, 10000.0, 0.5)
