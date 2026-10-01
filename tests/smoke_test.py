"""Funktionstest der Schreib- und Leselogik mit Attrappen — ohne Wolf-Cloud.

Aufruf: python3 tests/smoke_test.py (aus dem Wurzelverzeichnis des Repos)
"""

import asyncio
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import custom_components.heatwrite as ww
from homeassistant.exceptions import HomeAssistantError

# Dispatcher neutralisieren — ohne laufendes Home Assistant gibt es keinen Bus.
ww.async_dispatcher_send = lambda *a, **k: None


class P:
    """Zahlenparameter."""

    def __init__(self, vid, pid, name, ro=False, unit="°C"):
        self.value_id, self.parameter_id, self.name = vid, pid, name
        self.bundle_id, self.read_only, self.parent, self.unit = 1000, ro, "Heizung", unit


class LI:
    def __init__(self, value, name):
        self.value, self.name = value, name


class LP(P):
    """Auswahlparameter."""

    def __init__(self, vid, pid, name, items):
        super().__init__(vid, pid, name)
        self.items = items


class Value:
    def __init__(self, value_id, value):
        self.value_id, self.value = value_id, value


class ReadError(Exception):
    """Steht für wolf_comm.ParameterReadError."""


# Ohne installiertes wolf_comm ist der Import in der Komponente None geblieben.
ww.ParameterReadError = ReadError


class Client:
    def __init__(self):
        self.calls = []
        self.fetches = []
        self.fail = False
        self.cloud = {}
        self.unreadable = set()

    async def write_value(self, gw, sys_id, bundle, value):
        if self.fail:
            raise RuntimeError("API sagt nein")
        self.calls.append((gw, sys_id, bundle, value))
        self.cloud[value["ValueId"]] = value["State"]
        return {"ok": True}

    async def fetch_value(self, gw, sys_id, params):
        self.fetches.append([p.value_id for p in params])
        if any(p.value_id in self.unreadable for p in params):
            raise ReadError("Parameter kann nicht gelesen werden")
        return [
            Value(p.value_id, self.cloud[p.value_id])
            for p in params
            if p.value_id in self.cloud
        ]


class Coord:
    """Attrappe für den wolflink-Coordinator."""

    def __init__(self, data):
        self.data = data
        self.refreshes = 0

    async def async_request_refresh(self):
        self.refreshes += 1


class OwnCoord:
    """Attrappe für den eigenen DataUpdateCoordinator."""

    def __init__(self):
        self.data = None
        self.pushes = 0

    def async_set_updated_data(self, data):
        self.data = data
        self.pushes += 1


class Entry:
    options = {}
    entry_id = "test"


async def main():
    client = Client()
    ws = P(111, 340007, "Winter/Sommer Umschaltung")
    ro = P(222, 270125, "Nur lesbar", ro=True)
    dhw = LP(333, 350002, "1x Warmwasser", [LI(0, "Aus"), LI(1, "Ein")])
    kaputt = P(444, 380001, "Nicht lesbar")

    # Nur der Benutzerebenen-Parameter steckt in den wolflink-Daten. Die
    # Fachmann-Parameter (111, 333, 444) muss die Komponente selbst lesen —
    # genau der Fall, der in der Praxis „unavailable" erzeugt hat.
    coord = Coord({270125: (222, "100")})
    client.cloud = {111: "12.0", 222: "100", 333: "0", 444: "7"}
    client.unreadable = {444}

    # Zwei Parameter mit demselben Namen — im Expertenmodus der Normalfall,
    # etwa dieselbe Einstellung je Heizkreis.
    zwilling = P(555, 340007, "Winter/Sommer Umschaltung", ro=True)

    target = ww.WriteTarget(
        coord,
        client,
        5,
        7,
        "Wärmepumpe",
        {111: ws, 222: ro, 333: dhw, 444: kaputt, 555: zwilling},
    )
    # Rohbeschreibung der API: 555 ist entgegen der Bibliothek beschreibbar,
    # und zwar über eine andere BundleId. Die Gruppen unterscheiden die Namen.
    # Reihenfolge: name, prefix, group, read_only, bundle_id, min, max, step
    target.meta = {
        111: ww.ParamMeta("Winter/Sommer Umschaltung", None, "Mischerkreis 1", False, 1000, 0.0, 40.0, 0.5),
        222: ww.ParamMeta("Nur lesbar", "WP099", "Anlage", True, 1000),
        333: ww.ParamMeta("1x Warmwasser", None, "Warmwasser", False, 1000),
        444: ww.ParamMeta("Nicht lesbar", None, "Anlage", False, 1000),
        555: ww.ParamMeta("Winter/Sommer Umschaltung", None, "Direkter Heizkreis", False, 6300, 0.0, 40.0, 0.5),
    }
    target.rebuild_names()

    m = ww.WolfWriteManager.__new__(ww.WolfWriteManager)
    m.hass = None
    m.entry = Entry()
    m.targets = {7: target}
    m.history = []
    m.snapshots = {}
    m._lock = asyncio.Lock()
    m._writes = []
    m._polled = {}
    m.coordinator = OwnCoord()
    m._store = types.SimpleNamespace()
    m._async_save = lambda: asyncio.sleep(0)

    ok = fail = 0

    def check(label, cond):
        nonlocal ok, fail
        print(("PASS  " if cond else "FAIL  ") + label)
        ok, fail = (ok + bool(cond), fail + (not cond))

    # 0 — ohne eigenen Abruf hat ein Fachmann-Parameter keinen Wert
    check("Fachmann-Parameter ohne Abruf wertlos", target.current_value(ws) is None)
    check("Benutzerebene kommt von wolflink", target.current_value(ro) == "100")

    # 0b — Anmeldung und Abruf
    m.async_register_poll(7, 111)
    m.async_register_poll(7, 333)
    data = await m._async_poll()
    check("Abruf füllt den Wert", target.current_value(ws) == "12.0")
    check("Abruf liefert Momentaufnahme", data == {7: {111: "12.0", 333: "0"}})
    check("nur angemeldete Parameter abgerufen", client.fetches == [[111, 333]])

    # 0c — nicht lesbarer Parameter wird isoliert und gemerkt
    m.async_register_poll(7, 444)
    client.fetches.clear()
    await m._async_poll()
    check("nicht lesbarer Parameter gemerkt", target.unreadable == {444})
    check("lesbare Werte trotzdem da", target.current_value(ws) == "12.0")
    client.fetches.clear()
    await m._async_poll()
    check("gemerkter Parameter wird ausgelassen", client.fetches == [[111, 333]])
    m.async_unregister_poll(7, 444)

    # 0d — Abmelden nimmt den Parameter aus dem Abruf
    m.async_unregister_poll(7, 333)
    client.fetches.clear()
    await m._async_poll()
    check("Abmelden wirkt", client.fetches == [[111]])
    m.async_register_poll(7, 333)

    # 1 — normaler Schreibvorgang
    r = await m.async_write(target, ws, 13.0, source="test")
    check("schreibt und protokolliert", r["ergebnis"] == "ok" and len(client.calls) == 1)
    check(
        "Nutzlast korrekt",
        client.calls[0] == (5, 7, 1000, {"ValueId": 111, "State": "13.0"}),
    )
    check("alter Wert gemerkt", r["alt"] == "12.0" and r["neu"] == "13.0")
    check("Wert sofort übernommen", target.current_value(ws) == "13.0")
    check("Coordinator benachrichtigt", m.coordinator.pushes == 1)
    check("wolflink-Coordinator angestoßen", coord.refreshes == 1)

    # 2 — No-Op wird abgelehnt
    r = await m.async_write(target, ws, "13.0", source="test")
    check("No-Op wird nicht geschrieben", r["ergebnis"] == "unchanged" and len(client.calls) == 1)

    # 3 — readonly wird abgelehnt
    try:
        await m.async_write(target, ro, "50", source="test")
        check("readonly blockiert", False)
    except HomeAssistantError:
        check("readonly blockiert", True)

    # 4 — force überstimmt readonly
    r = await m.async_write(target, ro, "50", source="test", force=True)
    check("force überstimmt readonly", r["ergebnis"] == "ok" and len(client.calls) == 2)

    # 5 — dry_run schreibt nicht
    before = len(client.calls)
    r = await m.async_write(target, ws, 99, source="test", dry_run=True)
    check("dry_run schreibt nicht", r["ergebnis"] == "dry_run" and len(client.calls) == before)

    # 6 — Auswahlparameter über die Zahl
    r = await m.async_write(target, dhw, 1, source="test")
    check("Auswahlwert als Zahl gesendet", client.calls[-1][3] == {"ValueId": 333, "State": "1"})

    # 7 — Undo schreibt den alten Wert zurück
    n = len(client.calls)
    await m.async_undo(count=1)
    check(
        "undo schreibt zurück",
        client.calls[-1][3]["State"] == "0" and len(client.calls) == n + 1,
    )
    check("undo markiert Eintrag", any(e.get("rueckgaengig") for e in m.history))

    # 7b — Auswahlparameter über den Anzeigenamen
    await m.async_write(target, dhw, "Ein", source="test")
    check("Anzeigename wird auf Zahl übersetzt", client.calls[-1][3]["State"] == "1")
    r = await m.async_write(target, dhw, "Ein", source="test")
    check("No-Op erkennt Name gegen Zahl", r["ergebnis"] == "unchanged")

    # 7c — gelesener Anzeigename wird ebenfalls erkannt
    target.values[333] = "Ein"
    r = await m.async_write(target, dhw, 1, source="test")
    check("gelesener Name gegen Zahl erkannt", r["ergebnis"] == "unchanged")
    target.values[333] = "1"

    # 8 — Snapshot erfasst alle beschreibbaren Parameter, nicht nur angemeldete
    client.fetches.clear()
    snap = await m.async_snapshot_create("t1")
    # Auch 555 ist beschreibbar (laut Rohdaten), 444 fällt als nicht lesbar raus.
    check("Snapshot liest alle beschreibbaren nach", client.fetches == [[111, 333, 555]])
    check("Snapshot erfasst nur Parameter mit Wert", snap["anzahl"] == 2)

    # 9 — Ratenbegrenzung
    m.entry.options = {"max_writes_per_hour": 1}
    m._writes = [__import__("time").monotonic()]
    try:
        await m.async_write(target, ws, 55, source="test")
        check("Ratenbegrenzung greift", False)
    except ww.RateLimitExceeded:
        check("Ratenbegrenzung greift", True)
    m.entry.options = {}

    # 10 — Fehler der API landet in der Historie
    client.fail = True
    m._writes = []
    try:
        await m.async_write(target, ws, 44, source="test")
    except HomeAssistantError:
        pass
    check("Fehler protokolliert", m.history[-1]["ergebnis"] == "error")
    client.fail = False

    # 11 — Suche
    check("find über value_id", m.find(111)[1] is ws)
    check("find_by_name", m.find_by_name("1x Warmwasser")[1] is dhw)
    check("eindeutiger Name bleibt unverändert", target.display_name(dhw) == "1x Warmwasser")
    check("Parameternummer steht vorn", target.display_name(ro) == "WP099 Nur lesbar")
    check(
        "doppelter Name bekommt die Gruppe",
        target.display_name(ws) == "Winter/Sommer Umschaltung (Mischerkreis 1)",
    )
    check(
        "Anzeigename findet den richtigen",
        m.find_by_name("Winter/Sommer Umschaltung (Direkter Heizkreis)")[1] is zwilling,
    )
    try:
        m.find_by_name("Winter/Sommer Umschaltung")
        check("doppelter Name meldet beide Varianten", False)
    except HomeAssistantError as err:
        check(
            "doppelter Name meldet beide Varianten",
            "Mischerkreis 1" in str(err) and "Direkter Heizkreis" in str(err),
        )

    # 12 — Rohdaten überstimmen den Schreibschutz der Bibliothek
    check("Bibliothek meldet 555 als gesperrt", zwilling.read_only)
    check("Rohdaten geben 555 frei", not target.is_read_only(zwilling))
    check("555 zählt als beschreibbar", 555 in target.writable)
    check("BundleId kommt aus den Rohdaten", target.bundle_of(zwilling) == 6300)
    check("Grenzen kommen aus den Rohdaten", target.limits(ws) == (0.0, 40.0, 0.5))
    m._writes = []
    client.cloud[555] = "12"
    await m._async_fetch(target, [zwilling])
    r = await m.async_write(target, zwilling, 14, source="test")
    check("freigegebener Parameter wird geschrieben", r["ergebnis"] == "ok")
    check("Schreibvorgang nutzt die Rohdaten-BundleId", client.calls[-1][2] == 6300)
    try:
        m.find(999)
        check("find meldet Unbekanntes", False)
    except HomeAssistantError:
        check("find meldet Unbekanntes", True)

    print(f"\n{ok} bestanden, {fail} fehlgeschlagen")
    return fail


sys.exit(asyncio.run(main()))
