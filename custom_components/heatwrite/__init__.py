"""HeatWrite — Schreibzugriff auf die Wolf-Regelung.

Die offizielle Integration ``wolflink`` liest nur. Diese Komponente hängt sich
an deren bereits angemeldeten Client und ergänzt Schreibzugriff — mit
Änderungshistorie, Undo, Snapshots und mehreren Schutzmechanismen.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import ConfigEntryNotReady, HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

try:  # Bringt wolflink über seine Abhängigkeit mit; Import defensiv halten.
    from wolf_comm.wolf_client import ParameterReadError, WolfClient
except ImportError:  # pragma: no cover
    ParameterReadError = None  # type: ignore[assignment,misc]
    WolfClient = None  # type: ignore[assignment,misc]

from .const import (
    ATTR_COUNT,
    ATTR_DRY_RUN,
    ATTR_FORCE,
    ATTR_NAME,
    ATTR_SNAPSHOT,
    ATTR_SYSTEM_ID,
    ATTR_VALUE,
    ATTR_VALUE_ID,
    ATTR_VALUE_IDS,
    CONF_HISTORY_LIMIT,
    CONF_MAX_WRITES_PER_HOUR,
    CONF_SCAN_INTERVAL,
    DEFAULT_HISTORY_LIMIT,
    DEFAULT_MAX_WRITES_PER_HOUR,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    RESULT_DRY_RUN,
    RESULT_ERROR,
    RESULT_OK,
    RESULT_UNCHANGED,
    SERVICE_DESCRIBE_PARAMETER,
    SERVICE_RELOAD_PARAMETERS,
    SERVICE_SET_BY_NAME,
    SERVICE_SET_VALUE,
    SERVICE_SNAPSHOT_CREATE,
    SERVICE_SNAPSHOT_DELETE,
    SERVICE_SNAPSHOT_RESTORE,
    SERVICE_UNDO,
    SIGNAL_UPDATED,
    SOURCE_RESTORE,
    SOURCE_SERVICE,
    SOURCE_UNDO,
    STORAGE_KEY,
    STORAGE_VERSION,
    WOLFLINK_DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["button", "number", "select", "sensor"]

type WolfWriteConfigEntry = ConfigEntry[WolfWriteManager]


def _attr(obj: Any, *names: str, default: Any = None) -> Any:
    """Erstes vorhandenes Attribut zurückgeben.

    Die wolflink-Interna sind keine öffentliche Schnittstelle. Wir probieren
    mehrere Schreibweisen durch, damit ein Umbenennen in Home Assistant die
    Komponente nicht sofort zerlegt.
    """
    for name in names:
        if hasattr(obj, name):
            return getattr(obj, name)
    return default


def _number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None


def _clean_group(value: Any) -> str | None:
    """'058_Direkter Heizkreis' → 'Direkter Heizkreis'."""
    if not value:
        return None
    text = str(value).strip()
    head, sep, tail = text.partition("_")
    if sep and head.isdigit():
        text = tail.strip()
    return text or None


@dataclass
class ParamMeta:
    """Angaben aus der unveränderten Parameterbeschreibung der Wolf-API.

    Die Bibliothek verliert auf dem Weg zu ihren Parameter-Objekten einiges:
    den deutschen Namen, die Gruppe, Min/Max/Schrittweite — und sie behält bei
    mehrfach vorkommenden value_ids willkürlich die erste Variante, auch wenn
    eine andere denselben Parameter als beschreibbar meldet.
    """

    name: str | None = None
    #: Wolfs eigene Parameternummer, etwa 'WP025' oder 'A13'. Damit lässt sich
    #: jede Entität direkt dem Smart-Set-Ausdruck zuordnen.
    prefix: str | None = None
    group: str | None = None
    read_only: bool = True
    bundle_id: int | None = None
    minimum: float | None = None
    maximum: float | None = None
    step: float | None = None


@dataclass
class WriteTarget:
    """Ein beschreibbares Wolf-System samt Parameterliste."""

    coordinator: Any
    client: Any
    gateway_id: int
    system_id: int
    device_name: str
    parameters: dict[int, Any] = field(default_factory=dict)
    #: Eigener Abruf, value_id → Wert. wolflink liest nur die Benutzerebene;
    #: für die Fachmann-Parameter gibt es dort schlicht keinen Wert.
    values: dict[int, str] = field(default_factory=dict)
    #: value_ids, die die Wolf-API beim Lesen abgewiesen hat. Ein einziger
    #: solcher Parameter lässt den ganzen Bündelabruf scheitern, deshalb
    #: werden sie gemerkt und künftig ausgelassen.
    unreadable: set[int] = field(default_factory=set)
    #: value_id → Angaben aus der Rohbeschreibung der Wolf-API.
    meta: dict[int, ParamMeta] = field(default_factory=dict)
    #: value_id → Anzeigename. Mehrfach vorkommende Namen bekommen die Gruppe
    #: angehängt, und wenn das nicht reicht die value_id.
    names: dict[int, str] = field(default_factory=dict)

    def is_read_only(self, param: Any) -> bool:
        info = self.meta.get(int(param.value_id))
        return param.read_only if info is None else info.read_only

    def bundle_of(self, param: Any) -> int:
        info = self.meta.get(int(param.value_id))
        if info is not None and info.bundle_id is not None:
            return int(info.bundle_id)
        return int(param.bundle_id)

    def limits(self, param: Any) -> tuple[float | None, float | None, float | None]:
        info = self.meta.get(int(param.value_id))
        if info is None:
            return (None, None, None)
        return (info.minimum, info.maximum, info.step)

    @property
    def writable(self) -> dict[int, Any]:
        return {vid: p for vid, p in self.parameters.items() if not self.is_read_only(p)}

    def rebuild_names(self) -> None:
        """Anzeigenamen festlegen und Doppelte unterscheidbar machen.

        Der Rohname der API ist deutsch; die Bibliothek ersetzt ihn durch eine
        englische Übersetzung. Liefert die API eine Parameternummer mit, steht
        sie vorn — 'WP025 SG/PV'. Kommt ein Name dann immer noch mehrfach vor —
        dieselbe Einstellung je Heizkreis etwa —, hängen wir die Gruppe an.
        """
        base: dict[int, str] = {}
        for vid, param in self.parameters.items():
            info = self.meta.get(vid)
            label = (info.name if info and info.name else param.name) or str(vid)
            if info is not None and info.prefix:
                label = f"{info.prefix} {label}"
            base[vid] = label

        counts: dict[str, int] = {}
        for label in base.values():
            counts[label] = counts.get(label, 0) + 1

        labelled: dict[int, str] = {}
        for vid, label in base.items():
            info = self.meta.get(vid)
            if counts[label] > 1 and info is not None and info.group:
                labelled[vid] = f"{label} ({info.group})"
            else:
                labelled[vid] = label

        final_counts: dict[str, int] = {}
        for label in labelled.values():
            final_counts[label] = final_counts.get(label, 0) + 1
        self.names = {
            vid: (f"{label} ({vid})" if final_counts[label] > 1 else label)
            for vid, label in labelled.items()
        }

    def display_name(self, param: Any) -> str:
        return self.names.get(int(param.value_id), param.name)

    def current_value(self, param: Any) -> str | None:
        """Aktuellen Wert eines Parameters lesen.

        Zuerst aus dem eigenen Abruf, dann als Rückfall aus den Daten von
        wolflink — die decken die Benutzerebene ohne Zusatzaufwand ab.
        """
        own = self.values.get(int(param.value_id))
        if own is not None:
            return str(own)

        data = self.coordinator.data or {}
        entry = data.get(param.parameter_id)
        if entry and entry[0] == param.value_id:
            return str(entry[1])
        for value_id, value in data.values():
            if value_id == param.value_id:
                return str(value)
        return None


class RateLimitExceeded(HomeAssistantError):
    """Zu viele Schreibvorgänge in kurzer Zeit."""


class WolfWriteManager:
    """Hält die Ziele, schreibt Werte und führt Historie und Snapshots."""

    def __init__(self, hass: HomeAssistant, entry: WolfWriteConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.targets: dict[int, WriteTarget] = {}
        self.history: list[dict[str, Any]] = []
        self.snapshots: dict[str, dict[str, Any]] = {}
        self._store: Store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self._lock = asyncio.Lock()
        self._writes: list[float] = []
        #: system_id → value_ids, die abgerufen werden sollen. Nur aktivierte
        #: Entitäten melden sich hier an — die Expertenliste hat mehrere
        #: hundert Parameter, die will niemand alle pollen.
        self._polled: dict[int, set[int]] = {}
        self.coordinator: DataUpdateCoordinator = DataUpdateCoordinator(
            hass,
            _LOGGER,
            name=f"{DOMAIN} Werte",
            config_entry=entry,
            update_interval=timedelta(minutes=self.scan_interval),
            update_method=self._async_poll,
        )

    # --- Optionen ---------------------------------------------------------

    @property
    def history_limit(self) -> int:
        return int(self.entry.options.get(CONF_HISTORY_LIMIT, DEFAULT_HISTORY_LIMIT))

    @property
    def max_writes_per_hour(self) -> int:
        return int(
            self.entry.options.get(CONF_MAX_WRITES_PER_HOUR, DEFAULT_MAX_WRITES_PER_HOUR)
        )

    @property
    def scan_interval(self) -> int:
        return int(self.entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL))

    # --- Aufbau -----------------------------------------------------------

    async def async_load(self) -> None:
        """Gespeicherte Historie und Snapshots laden."""
        data = await self._store.async_load() or {}
        self.history = data.get("history", [])
        self.snapshots = data.get("snapshots", {})

    async def _async_save(self) -> None:
        await self._store.async_save(
            {"history": self.history, "snapshots": self.snapshots}
        )

    async def async_discover(self) -> None:
        """wolflink-Koordinatoren finden und die Parameterliste einlesen."""
        entries = self.hass.config_entries.async_entries(WOLFLINK_DOMAIN)
        if not entries:
            raise ConfigEntryNotReady(
                "Die Integration 'Wolf SmartSet Service' (wolflink) ist nicht eingerichtet."
            )

        targets: dict[int, WriteTarget] = {}
        for wolf_entry in entries:
            runtime = getattr(wolf_entry, "runtime_data", None)
            if not isinstance(runtime, dict) or not runtime:
                continue
            for coordinator in runtime.values():
                client = _attr(coordinator, "_wolf_client", "wolf_client")
                gateway_id = _attr(coordinator, "_gateway_id", "gateway_id")
                system_id = _attr(coordinator, "device_id", "_device_id")
                name = _attr(coordinator, "device_name", "_device_name", default="Wolf")
                if client is None or gateway_id is None or system_id is None:
                    _LOGGER.warning(
                        "wolflink-Coordinator ohne erwartete Attribute — "
                        "vermutlich hat sich die Integration geändert"
                    )
                    continue
                targets[int(system_id)] = WriteTarget(
                    coordinator=coordinator,
                    client=client,
                    gateway_id=int(gateway_id),
                    system_id=int(system_id),
                    device_name=str(name),
                )

        if not targets:
            raise ConfigEntryNotReady(
                "Kein nutzbarer wolflink-Coordinator gefunden. Läuft die "
                "Integration 'Wolf SmartSet Service'?"
            )

        self.targets = targets
        await self.async_reload_parameters()

    async def async_reload_parameters(self) -> None:
        """Vollständige Parameterliste inklusive Fachmann-Ebene holen.

        Die wolflink-Integration erzeugt ihren Client ohne Expertenmodus und
        sieht deshalb nur die Benutzerebene. Wir schalten das Attribut für die
        Dauer des Abrufs um und setzen es garantiert zurück.
        """
        async with self._lock:
            for target in self.targets.values():
                client = target.client
                previous = getattr(client, "expert_mode", False)
                try:
                    client.expert_mode = True
                    params = await client.fetch_parameters(
                        target.gateway_id, target.system_id
                    )
                except Exception as err:  # noqa: BLE001 — Bibliotheksfehler sind vielfältig
                    _LOGGER.error(
                        "Parameterliste für %s konnte nicht gelesen werden: %s",
                        target.device_name,
                        err,
                    )
                    continue
                finally:
                    client.expert_mode = previous

                target.parameters = {int(p.value_id): p for p in params if p is not None}
                await self._async_load_meta(target)
                target.rebuild_names()
                _LOGGER.info(
                    "%s: %s Parameter gelesen, davon %s beschreibbar",
                    target.device_name,
                    len(target.parameters),
                    len(target.writable),
                )

    async def _async_raw_description(self, target: WriteTarget) -> Any:
        """Die unveränderte Antwort von GetGuiDescriptionForGateway holen."""
        request = getattr(target.client, "_WolfClient__request", None)
        if request is None:
            raise HomeAssistantError(
                "Der Rohabruf ist nicht möglich — die Bibliothek wolf_comm hat "
                "sich geändert."
            )
        return await request(
            "get",
            "api/portal/GetGuiDescriptionForGateway",
            params={"GatewayId": target.gateway_id, "SystemId": target.system_id},
        )

    async def _async_load_meta(self, target: WriteTarget) -> None:
        """Namen, Gruppen, Grenzen und Schreibrechte aus den Rohdaten holen.

        Die Bibliothek behält bei mehrfach vorkommenden value_ids die zuerst
        einsortierte Variante. Steht derselbe Parameter in einer anderen Gruppe
        als beschreibbar, geht das verloren — und zwar rein nach Sortierglück.
        Hier gewinnt die beschreibbare Variante, samt ihrer BundleId.
        """
        if WolfClient is None:
            return
        try:
            description = await self._async_raw_description(target)
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning(
                "Rohbeschreibung für %s nicht lesbar, es gelten die Angaben der "
                "Bibliothek: %s",
                target.device_name,
                err,
            )
            return

        meta: dict[int, ParamMeta] = {}
        for descriptor in WolfClient._extract_parameter_descriptors(description):
            value_id = descriptor.get("ValueId")
            if value_id is None or int(value_id) not in target.parameters:
                continue
            value_id = int(value_id)
            candidate = ParamMeta(
                name=(str(descriptor["Name"]).strip() if descriptor.get("Name") else None),
                prefix=(
                    str(descriptor["NamePrefix"]).strip()
                    if descriptor.get("NamePrefix")
                    else None
                ),
                group=_clean_group(descriptor.get("Group")),
                read_only=bool(descriptor.get("IsReadOnly", True)),
                bundle_id=descriptor.get("BundleId"),
                minimum=_number(descriptor.get("MinValue")),
                maximum=_number(descriptor.get("MaxValue")),
                step=_number(descriptor.get("StepWidth")),
            )
            known = meta.get(value_id)
            if known is None or (known.read_only and not candidate.read_only):
                meta[value_id] = candidate

        target.meta = meta
        freed = sum(
            1
            for vid, param in target.parameters.items()
            if param.read_only and vid in meta and not meta[vid].read_only
        )
        if freed:
            _LOGGER.info(
                "%s: %s Parameter sind laut Rohdaten doch beschreibbar",
                target.device_name,
                freed,
            )

    # --- Werte abrufen ----------------------------------------------------

    def async_register_poll(self, system_id: int, value_id: int) -> None:
        """Eine Entität meldet sich für den Werteabruf an."""
        self._polled.setdefault(int(system_id), set()).add(int(value_id))

    def async_unregister_poll(self, system_id: int, value_id: int) -> None:
        ids = self._polled.get(int(system_id))
        if ids is not None:
            ids.discard(int(value_id))

    def _snapshot_of_values(self) -> dict[int, dict[int, str]]:
        return {sid: dict(t.values) for sid, t in self.targets.items()}

    async def _async_poll(self) -> dict[int, dict[int, str]]:
        """Die angemeldeten Parameter aus der Wolf-Cloud lesen."""
        errors: list[str] = []
        for target in self.targets.values():
            wanted = self._polled.get(target.system_id, set())
            params = [
                target.parameters[vid]
                for vid in sorted(wanted)
                if vid in target.parameters and vid not in target.unreadable
            ]
            if not params:
                continue
            error = await self._async_refresh_values(target, params)
            if error is not None:
                errors.append(f"{target.device_name}: {error}")

        if errors and not any(t.values for t in self.targets.values()):
            raise UpdateFailed("; ".join(errors))
        if errors:
            _LOGGER.warning("Werteabruf teilweise fehlgeschlagen: %s", "; ".join(errors))
        return self._snapshot_of_values()

    async def _async_refresh_values(self, target: WriteTarget, params: list[Any]) -> str | None:
        """Werte holen. Gibt None zurück oder die Fehlermeldung als Text."""
        try:
            await self._async_fetch(target, params)
        except Exception as err:  # noqa: BLE001 — Bibliotheksfehler sind vielfältig
            read_error = ParameterReadError is not None and isinstance(
                err, ParameterReadError
            )
            if read_error and len(params) > 1:
                # Ein nicht lesbarer Parameter reißt das ganze Bündel mit.
                # Einzeln nachfassen, den Störenfried merken, künftig auslassen.
                await self._async_isolate(target, params)
                return None
            if read_error:
                target.unreadable.add(int(params[0].value_id))
                _LOGGER.warning(
                    "'%s' (value_id %s) lässt sich nicht lesen und wird künftig "
                    "übersprungen: %s",
                    params[0].name,
                    params[0].value_id,
                    err,
                )
                return None
            return str(err)
        return None

    async def _async_isolate(self, target: WriteTarget, params: list[Any]) -> None:
        for param in params:
            await self._async_refresh_values(target, [param])

    async def _async_fetch(self, target: WriteTarget, params: list[Any]) -> None:
        async with self._lock:
            values = await target.client.fetch_value(
                target.gateway_id, target.system_id, list(params)
            )
        for value in values:
            target.values[int(value.value_id)] = str(value.value)

    # --- Rohdaten der Parameterbeschreibung -------------------------------

    async def async_describe(
        self, value_ids: list[int], system_id: int | None = None
    ) -> dict[str, Any]:
        """Die unveränderte Parameterbeschreibung der Wolf-API ausgeben.

        Die Bibliothek wertet ``IsReadOnly`` aus und setzt einen fehlenden
        Eintrag vorsichtshalber auf „schreibgeschützt". Ob die API das Feld
        tatsächlich mitliefert, sieht man nur an den Rohdaten.
        """
        if WolfClient is None:
            raise HomeAssistantError("wolf_comm ist nicht verfügbar.")

        wanted = {int(v) for v in value_ids}
        found: list[dict[str, Any]] = []
        for target in self.targets.values():
            if system_id is not None and target.system_id != int(system_id):
                continue
            async with self._lock:
                description = await self._async_raw_description(target)
            for descriptor in WolfClient._extract_parameter_descriptors(description):
                if int(descriptor.get("ValueId", -1)) not in wanted:
                    continue
                param = target.parameters.get(int(descriptor["ValueId"]))
                found.append(
                    {
                        "system_id": target.system_id,
                        "name": None if param is None else target.display_name(param),
                        "schreibgeschuetzt": None
                        if param is None
                        else target.is_read_only(param),
                        "laut_bibliothek": None if param is None else param.read_only,
                        "isreadonly_vorhanden": "IsReadOnly" in descriptor,
                        "rohdaten": descriptor,
                    }
                )

        if not found:
            raise HomeAssistantError(
                "Keine Beschreibung zu diesen value_ids gefunden: "
                + ", ".join(str(v) for v in sorted(wanted))
            )
        return {"parameter": found, "anzahl": len(found)}

    # --- Suche ------------------------------------------------------------

    def find(self, value_id: int, system_id: int | None = None) -> tuple[WriteTarget, Any]:
        for target in self.targets.values():
            if system_id is not None and target.system_id != int(system_id):
                continue
            param = target.parameters.get(int(value_id))
            if param is not None:
                return target, param
        raise HomeAssistantError(f"Kein Parameter mit der value_id {value_id} gefunden.")

    def find_by_name(
        self, name: str, system_id: int | None = None
    ) -> tuple[WriteTarget, Any]:
        needle = name.strip().casefold()
        candidates: list[tuple[WriteTarget, Any]] = []
        for target in self.targets.values():
            if system_id is not None and target.system_id != int(system_id):
                continue
            candidates.extend((target, param) for param in target.parameters.values())

        # Zuerst über den Anzeigenamen — der ist durch die angehängte value_id
        # eindeutig, wenn derselbe Parametername mehrfach vorkommt.
        exact = [
            (t, p) for t, p in candidates if t.display_name(p).strip().casefold() == needle
        ]
        if len(exact) == 1:
            return exact[0]

        hits = [(t, p) for t, p in candidates if p.name.strip().casefold() == needle]
        if not hits:
            raise HomeAssistantError(f"Kein Parameter mit dem Namen '{name}' gefunden.")
        if len(hits) > 1:
            variants = ", ".join(f"'{t.display_name(p)}'" for t, p in hits)
            raise HomeAssistantError(
                f"Der Name '{name}' ist nicht eindeutig. Gemeint sein kann: {variants}. "
                "Einen davon angeben oder set_value mit der value_id benutzen."
            )
        return hits[0]

    # --- Schreiben --------------------------------------------------------

    def _check_rate_limit(self) -> None:
        now = time.monotonic()
        self._writes = [t for t in self._writes if now - t < 3600]
        if len(self._writes) >= self.max_writes_per_hour:
            raise RateLimitExceeded(
                f"Ratenbegrenzung erreicht: {self.max_writes_per_hour} Schreibvorgänge "
                "pro Stunde. Schutz gegen Automationsschleifen und EEPROM-Verschleiß. "
                "In den Optionen der Integration änderbar."
            )

    @staticmethod
    def _as_state(param: Any, value: Any) -> str:
        """Wert in die Form bringen, die die Wolf-API erwartet.

        Auswahlparameter werden intern über ihre Zahl geschrieben, gelesen
        werden sie als Anzeigename. Beides wird hier auf die Zahl normiert,
        damit Vergleich und Schreibvorgang dieselbe Sprache sprechen.
        """
        items = getattr(param, "items", None)
        if items:
            text = str(value).strip()
            for item in items:
                if str(item.value) == text:
                    return str(item.value)
            for item in items:
                if item.name.strip().casefold() == text.casefold():
                    return str(item.value)
        if isinstance(value, bool):
            return "1" if value else "0"
        return str(value)

    async def async_write(
        self,
        target: WriteTarget,
        param: Any,
        value: Any,
        *,
        source: str,
        dry_run: bool = False,
        force: bool = False,
        bypass_noop: bool = False,
    ) -> dict[str, Any]:
        """Einen Parameter schreiben — mit allen Schutzprüfungen."""
        new_state = self._as_state(param, value)
        raw_old = target.current_value(param)
        old_state = None if raw_old is None else self._as_state(param, raw_old)

        record: dict[str, Any] = {
            "zeit": dt_util.utcnow().isoformat(),
            "geraet": target.device_name,
            "system_id": target.system_id,
            "parameter": target.display_name(param),
            "value_id": int(param.value_id),
            "parameter_id": int(param.parameter_id),
            "alt": old_state,
            "neu": new_state,
            "quelle": source,
            "ergebnis": RESULT_OK,
            "rueckgaengig": False,
        }

        if target.is_read_only(param) and not force:
            raise HomeAssistantError(
                f"'{target.display_name(param)}' meldet die Wolf-API als nicht "
                "beschreibbar. Mit force: true lässt sich der Versuch erzwingen."
            )

        if old_state is not None and old_state == new_state and not force and not bypass_noop:
            record["ergebnis"] = RESULT_UNCHANGED
            _LOGGER.debug(
                "'%s' steht bereits auf %s — kein Schreibvorgang (EEPROM-Schonung)",
                param.name,
                new_state,
            )
            return record

        if dry_run:
            record["ergebnis"] = RESULT_DRY_RUN
            return record

        self._check_rate_limit()

        async with self._lock:
            try:
                await target.client.write_value(
                    target.gateway_id,
                    target.system_id,
                    target.bundle_of(param),
                    {"ValueId": int(param.value_id), "State": new_state},
                )
            except Exception as err:  # noqa: BLE001
                record["ergebnis"] = RESULT_ERROR
                record["fehler"] = str(err)
                await self._record(record)
                raise HomeAssistantError(
                    f"Schreiben von '{param.name}' fehlgeschlagen: {err}"
                ) from err
            self._writes.append(time.monotonic())

        # Den geschriebenen Wert sofort übernehmen. Die Wolf-Cloud meldet ihn
        # erst nach dem nächsten Abruf zurück; bis dahin zeigte die Entität
        # sonst den alten Wert. Der nächste Abruf korrigiert notfalls.
        target.values[int(param.value_id)] = new_state

        await self._record(record)
        self.coordinator.async_set_updated_data(self._snapshot_of_values())
        await target.coordinator.async_request_refresh()
        return record

    async def _record(self, record: dict[str, Any]) -> None:
        self.history.append(record)
        if len(self.history) > self.history_limit:
            self.history = self.history[-self.history_limit :]
        await self._async_save()
        async_dispatcher_send(self.hass, SIGNAL_UPDATED)

    # --- Undo -------------------------------------------------------------

    async def async_undo(self, count: int = 1, dry_run: bool = False) -> list[dict[str, Any]]:
        """Die letzten erfolgreichen Änderungen zurücknehmen."""
        done: list[dict[str, Any]] = []
        for record in reversed(self.history):
            if len(done) >= count:
                break
            if record.get("ergebnis") != RESULT_OK or record.get("rueckgaengig"):
                continue
            if record.get("alt") is None:
                continue
            target, param = self.find(record["value_id"], record.get("system_id"))
            # bypass_noop: Der neue Wert erscheint in den Coordinator-Daten erst
            # nach dem nächsten Cloud-Poll, bis zu 90 Sekunden später. Ohne das
            # würde ein schnelles Rückgängig als "unverändert" abgetan und
            # stillschweigend nichts tun.
            result = await self.async_write(
                target,
                param,
                record["alt"],
                source=SOURCE_UNDO,
                dry_run=dry_run,
                bypass_noop=True,
            )
            if not dry_run:
                record["rueckgaengig"] = True
            done.append(result)

        if not done:
            raise HomeAssistantError("Keine rücknehmbare Änderung in der Historie.")
        if not dry_run:
            await self._async_save()
        return done

    # --- Snapshots --------------------------------------------------------

    async def async_snapshot_create(self, name: str) -> dict[str, Any]:
        values: list[dict[str, Any]] = []
        for target in self.targets.values():
            # Ein Snapshot soll alle beschreibbaren Parameter erfassen, nicht
            # nur die, für die gerade eine Entität aktiviert ist. Also einmal
            # vollständig lesen.
            params = [
                param
                for vid, param in target.writable.items()
                if vid not in target.unreadable
            ]
            if params:
                error = await self._async_refresh_values(target, params)
                if error is not None:
                    raise HomeAssistantError(
                        f"Werte für '{target.device_name}' konnten nicht gelesen "
                        f"werden, Snapshot abgebrochen: {error}"
                    )
            for param in target.writable.values():
                current = target.current_value(param)
                if current is None:
                    continue
                values.append(
                    {
                        "system_id": target.system_id,
                        "value_id": int(param.value_id),
                        "parameter": target.display_name(param),
                        "wert": current,
                    }
                )
        snapshot = {
            "zeit": dt_util.utcnow().isoformat(),
            "anzahl": len(values),
            "werte": values,
        }
        self.snapshots[name] = snapshot
        await self._async_save()
        async_dispatcher_send(self.hass, SIGNAL_UPDATED)
        _LOGGER.info("Snapshot '%s' mit %s Werten angelegt", name, len(values))
        return {"name": name, "anzahl": len(values)}

    async def async_snapshot_restore(
        self, name: str, dry_run: bool = False
    ) -> list[dict[str, Any]]:
        snapshot = self.snapshots.get(name)
        if snapshot is None:
            raise HomeAssistantError(f"Snapshot '{name}' existiert nicht.")

        if not dry_run:
            # Das Zurückrollen selbst umkehrbar machen.
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
            await self.async_snapshot_create(f"vor-wiederherstellung-{stamp}")

        results: list[dict[str, Any]] = []
        for item in snapshot["werte"]:
            try:
                target, param = self.find(item["value_id"], item.get("system_id"))
            except HomeAssistantError:
                _LOGGER.warning(
                    "Snapshot-Eintrag %s (%s) existiert nicht mehr — übersprungen",
                    item["value_id"],
                    item.get("parameter"),
                )
                continue
            result = await self.async_write(
                target,
                param,
                item["wert"],
                source=SOURCE_RESTORE,
                dry_run=dry_run,
            )
            if result["ergebnis"] != RESULT_UNCHANGED:
                results.append(result)
        return results

    async def async_snapshot_delete(self, name: str) -> None:
        if self.snapshots.pop(name, None) is None:
            raise HomeAssistantError(f"Snapshot '{name}' existiert nicht.")
        await self._async_save()
        async_dispatcher_send(self.hass, SIGNAL_UPDATED)


# --- Home-Assistant-Anbindung --------------------------------------------


async def async_setup_entry(hass: HomeAssistant, entry: WolfWriteConfigEntry) -> bool:
    """Integration einrichten."""
    manager = WolfWriteManager(hass, entry)
    await manager.async_load()
    await manager.async_discover()

    entry.runtime_data = manager
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    _async_register_services(hass)
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: WolfWriteConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded and not [
        e for e in hass.config_entries.async_entries(DOMAIN) if e.entry_id != entry.entry_id
    ]:
        for service in (
            SERVICE_SET_VALUE,
            SERVICE_SET_BY_NAME,
            SERVICE_UNDO,
            SERVICE_SNAPSHOT_CREATE,
            SERVICE_SNAPSHOT_RESTORE,
            SERVICE_SNAPSHOT_DELETE,
            SERVICE_RELOAD_PARAMETERS,
            SERVICE_DESCRIBE_PARAMETER,
        ):
            hass.services.async_remove(DOMAIN, service)
    return unloaded


async def _async_options_updated(hass: HomeAssistant, entry: WolfWriteConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


def _managers(hass: HomeAssistant) -> list[WolfWriteManager]:
    return [
        entry.runtime_data
        for entry in hass.config_entries.async_entries(DOMAIN)
        if getattr(entry, "runtime_data", None) is not None
    ]


def _manager(hass: HomeAssistant) -> WolfWriteManager:
    managers = _managers(hass)
    if not managers:
        raise HomeAssistantError("HeatWrite ist nicht eingerichtet.")
    return managers[0]


def _async_register_services(hass: HomeAssistant) -> None:
    if hass.services.has_service(DOMAIN, SERVICE_SET_VALUE):
        return

    async def set_value(call: ServiceCall) -> ServiceResponse:
        manager = _manager(hass)
        target, param = manager.find(call.data[ATTR_VALUE_ID], call.data.get(ATTR_SYSTEM_ID))
        record = await manager.async_write(
            target,
            param,
            call.data[ATTR_VALUE],
            source=SOURCE_SERVICE,
            dry_run=call.data.get(ATTR_DRY_RUN, False),
            force=call.data.get(ATTR_FORCE, False),
        )
        return dict(record)

    async def set_by_name(call: ServiceCall) -> ServiceResponse:
        manager = _manager(hass)
        target, param = manager.find_by_name(
            call.data[ATTR_NAME], call.data.get(ATTR_SYSTEM_ID)
        )
        record = await manager.async_write(
            target,
            param,
            call.data[ATTR_VALUE],
            source=SOURCE_SERVICE,
            dry_run=call.data.get(ATTR_DRY_RUN, False),
            force=call.data.get(ATTR_FORCE, False),
        )
        return dict(record)

    async def undo(call: ServiceCall) -> ServiceResponse:
        manager = _manager(hass)
        results = await manager.async_undo(
            count=call.data.get(ATTR_COUNT, 1),
            dry_run=call.data.get(ATTR_DRY_RUN, False),
        )
        return {"rueckgenommen": results}

    async def snapshot_create(call: ServiceCall) -> ServiceResponse:
        manager = _manager(hass)
        return await manager.async_snapshot_create(call.data[ATTR_SNAPSHOT])

    async def snapshot_restore(call: ServiceCall) -> ServiceResponse:
        manager = _manager(hass)
        results = await manager.async_snapshot_restore(
            call.data[ATTR_SNAPSHOT], dry_run=call.data.get(ATTR_DRY_RUN, False)
        )
        return {"geaendert": results, "anzahl": len(results)}

    async def snapshot_delete(call: ServiceCall) -> None:
        await _manager(hass).async_snapshot_delete(call.data[ATTR_SNAPSHOT])

    async def describe_parameter(call: ServiceCall) -> ServiceResponse:
        manager = _manager(hass)
        return await manager.async_describe(
            call.data[ATTR_VALUE_IDS], call.data.get(ATTR_SYSTEM_ID)
        )

    async def reload_parameters(call: ServiceCall) -> None:
        # Der Reload des Config-Entries ruft async_discover() erneut auf und
        # liest die Parameterliste damit ohnehin neu ein.
        for entry in hass.config_entries.async_entries(DOMAIN):
            await hass.config_entries.async_reload(entry.entry_id)

    base = {
        vol.Optional(ATTR_SYSTEM_ID): vol.Coerce(int),
        vol.Optional(ATTR_DRY_RUN, default=False): cv.boolean,
        vol.Optional(ATTR_FORCE, default=False): cv.boolean,
    }

    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_VALUE,
        set_value,
        schema=vol.Schema(
            {
                vol.Required(ATTR_VALUE_ID): vol.Coerce(int),
                vol.Required(ATTR_VALUE): vol.Any(cv.string, vol.Coerce(float)),
                **base,
            }
        ),
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_BY_NAME,
        set_by_name,
        schema=vol.Schema(
            {
                vol.Required(ATTR_NAME): cv.string,
                vol.Required(ATTR_VALUE): vol.Any(cv.string, vol.Coerce(float)),
                **base,
            }
        ),
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_UNDO,
        undo,
        schema=vol.Schema(
            {
                vol.Optional(ATTR_COUNT, default=1): vol.All(
                    vol.Coerce(int), vol.Range(min=1, max=50)
                ),
                vol.Optional(ATTR_DRY_RUN, default=False): cv.boolean,
            }
        ),
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_SNAPSHOT_CREATE,
        snapshot_create,
        schema=vol.Schema({vol.Required(ATTR_SNAPSHOT): cv.string}),
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_SNAPSHOT_RESTORE,
        snapshot_restore,
        schema=vol.Schema(
            {
                vol.Required(ATTR_SNAPSHOT): cv.string,
                vol.Optional(ATTR_DRY_RUN, default=False): cv.boolean,
            }
        ),
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_SNAPSHOT_DELETE,
        snapshot_delete,
        schema=vol.Schema({vol.Required(ATTR_SNAPSHOT): cv.string}),
    )
    hass.services.async_register(
        DOMAIN, SERVICE_RELOAD_PARAMETERS, reload_parameters, schema=vol.Schema({})
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_DESCRIBE_PARAMETER,
        describe_parameter,
        schema=vol.Schema(
            {
                vol.Required(ATTR_VALUE_IDS): vol.All(
                    cv.ensure_list, [vol.Coerce(int)], vol.Length(min=1, max=20)
                ),
                vol.Optional(ATTR_SYSTEM_ID): vol.Coerce(int),
            }
        ),
        supports_response=SupportsResponse.ONLY,
    )
