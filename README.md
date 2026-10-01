# HeatWrite

**Schreibzugriff für Wolf-Heizungen über Smartset — in Home Assistant.**

> Unabhängiges Projekt. Kein Produkt der WOLF GmbH und nicht von ihr geprüft,
> unterstützt oder herausgegeben.

Die offizielle Integration **Wolf SmartSet Service** (`wolflink`) liest nur. Diese
Komponente hängt sich an deren bereits angemeldeten Client und ergänzt das
Schreiben — mit Änderungshistorie, Undo, Snapshots und mehreren
Schutzmechanismen.

Keine zweite Anmeldung, keine doppelt hinterlegten Zugangsdaten, keine zweite
Cloud-Session.

## Bitte vorher lesen

**Diese Komponente verstellt Parameter deiner Heizungsregelung.** Falsch gesetzte
Fachmann-Parameter können die Anlage ineffizient laufen lassen, den Komfort
ruinieren oder Schaden anrichten. Du benutzt sie auf eigene Verantwortung.

**Keine Haftung.** Weder für die Funktion noch für Fehler, Fehlbedienung,
Fehlsteuerung oder deren Folgen — an der Anlage, am Gebäude oder an der
Stromrechnung. Keine Gewährleistung, keine Zusicherung irgendeiner
Eigenschaft. Siehe auch die MIT-Lizenz.

**Entwickelt und getestet an genau einer Anlage:** Wolf CHA-10 Monoblock mit
BM-2-Regelung, angebunden über Wolf Smartset. Dort laufen 241 Parameter ein,
117 davon beschreibbar. Auf anderen Geräten sollte es funktionieren, weil die
Komponente kein Modell kennt, sondern die Parameterbeschreibung nimmt, die die
Wolf-Cloud für die jeweilige Anlage liefert — geprüft ist das aber nicht.
Rückmeldungen sind willkommen.

**Nur eine Anlage pro Konto.** Hängen mehrere Wolf-Systeme am selben
Smartset-Konto, wiederholen sich die Parameternamen je System, und
`set_by_name` oder `set_value` ohne Angabe von `system_id` können das falsche
System erwischen. Die Entitäten selbst bleiben pro Gerät getrennt. Getestet ist
dieser Fall nicht — wer ihn hat, gibt bei jedem Service-Aufruf die `system_id`
mit.

**Es ist keine offizielle Wolf-Software.** Genutzt wird die Schnittstelle des
Wolf-Smartset-Dienstes, an dem die Anlage ohnehin angemeldet ist. Ändert Wolf
daran etwas, kann es sein, dass HeatWrite nicht mehr läuft.

## Was sie kann

* **Alle Parameter, nicht nur die Benutzerebene.** `wolflink` liest die
  Parameterliste ohne Expertenmodus und sieht deshalb nur den Benutzerbereich.
  Diese Komponente holt die vollständige Liste — Fachmann-Parameter wie
  Winter/Sommer-Umschaltung, Sparfaktor, Heizkurve und die WP-Parameter
  inklusive.
* **Echte Entitäten** statt roher IDs: `number` für Zahlenparameter, `select`
  für Auswahlparameter. Damit lässt sich zum Beispiel „1x Warmwasser" aus einer
  Automation auslösen.
* **Eigener Werteabruf.** `wolflink` liest nur die Parameter, die es selbst
  kennt — für die Fachmann-Parameter gibt es dort keinen Wert. Diese Komponente
  ruft die Werte selbst ab, und zwar nur für Parameter mit aktivierter Entität.
* **Unveränderte Parameterbeschreibung.** Die Bibliothek `wolf_comm` verliert
  auf dem Weg zu ihren Objekten den deutschen Namen, die Gruppe und die
  Wertegrenzen — und bei mehrfach vorkommenden `value_id`s behält sie die
  zuerst einsortierte Variante, auch wenn eine andere denselben Parameter als
  beschreibbar meldet. Diese Komponente liest die Beschreibung zusätzlich roh
  und holt all das zurück — inklusive Wolfs eigener Parameternummer, die den
  Entitäten vorangestellt wird: „WP025 SG/PV".
* **Services** für alles, was sich nicht als Entität abbilden lässt.

## Voraussetzungen

* Home Assistant 2026.9 oder neuer
* Die Integration **Wolf SmartSet Service** (`wolflink`) eingerichtet und
  geladen. Diese Komponente hängt sich an deren Client; ohne ihn bricht die
  Einrichtung mit einer Fehlermeldung ab.
* Ein Wolf-Gateway, das die Anlage an Smartset anbindet: Link Home / ISM7,
  Link Pro / ISM7e oder Link Home Pro.

Welche Heizgeräte das sind, entscheidet `wolflink`, nicht diese Komponente.
Laut Home-Assistant-Dokumentation sind das Heizungs-, Wärmepumpen- und
Solarthermieanlagen; ausdrücklich getestet werden dort FGB-28, COB-20, CGB-2
und CGB-2-14 genannt.

Das ISM7 nimmt **nur eine Verbindung gleichzeitig** an. Eine parallel laufende
MQTT-Brücke wie `ism7mqtt` schließt Smartset — und damit diese Komponente —
aus.

## Installation

Über HACS als benutzerdefiniertes Repository:

1. HACS → Integrationen → Dreipunktmenü → Benutzerdefinierte Repositories
2. `https://github.com/mike-139/heatwrite` eintragen, Kategorie **Integration**
3. Herunterladen, Home Assistant neu starten
4. Einstellungen → Geräte & Dienste → Integration hinzufügen → **HeatWrite**

Der Einrichtungsdialog hat kein Eingabefeld. Er prüft nur, ob `wolflink`
eingerichtet und geladen ist.

## Entitäten

Alle Parameter-Entitäten sind **standardmäßig deaktiviert**. Die Expertenliste
umfasst je nach Anlage mehrere hundert Parameter — aktiviere gezielt, was du
brauchst, unter Geräte & Dienste → HeatWrite → Entitäten.

Abgerufen werden ausschließlich die Parameter aktivierter Entitäten, standardmäßig
alle fünf Minuten (in den Optionen einstellbar). Eine gerade aktivierte Entität
steht für einen Moment auf „nicht verfügbar", bis der erste Abruf durch ist.

| Wolf-Parameter | Entität |
|---|---|
| beschreibbarer Auswahlparameter (`ListItemParameter`) | `select` |
| beschreibbarer Zahlenparameter | `number` |
| schreibgeschützter Parameter | `sensor` |

Dazu zwei Diagnose-Sensoren: **Letzte Änderung** (mit dem Verlauf als Attribut)
und **Snapshots**, sowie zwei Knöpfe: **Letzte Änderung rückgängig** (nur
drückbar, wenn es etwas zurückzunehmen gibt) und **Snapshot anlegen**.

Was die Wolf-API als schreibgeschützt meldet, gibt es nur lesend — das betrifft
auch Werte, die in Smart Set einstellbar aussehen.

## Services

| Service | Zweck |
|---|---|
| `heatwrite.set_value` | Parameter über `value_id` schreiben |
| `heatwrite.set_by_name` | Parameter über seinen Namen schreiben |
| `heatwrite.undo` | Die letzten Änderungen zurücknehmen |
| `heatwrite.snapshot_create` | Stand aller beschreibbaren Parameter sichern |
| `heatwrite.snapshot_restore` | Auf einen Snapshot zurück |
| `heatwrite.snapshot_delete` | Snapshot entfernen |
| `heatwrite.reload_parameters` | Parameterliste neu einlesen |
| `heatwrite.describe_parameter` | Rohbeschreibung eines Parameters ausgeben |

Alle schreibenden Services nehmen `dry_run: true` und melden dann nur, was sie
täten.

```yaml
# Warmwasserladung bei PV-Überschuss auslösen
action: heatwrite.set_by_name
data:
  name: 1x Warmwasser
  value: 1
```

```yaml
# Vor dem Herumprobieren absichern
action: heatwrite.snapshot_create
data:
  snapshot: vor-optimierung
```

## Schutzmechanismen

* **Schreibschutz respektiert.** Parameter, die die Wolf-API als `IsReadOnly`
  meldet, werden abgelehnt. `force: true` erzwingt den Versuch.
* **Kein No-Op-Schreiben.** Steht der Parameter bereits auf dem Zielwert,
  passiert nichts. Jeder Schreibvorgang landet im EEPROM der BM-2, und das hat
  endlich viele Zyklen.
* **Ratenbegrenzung**, Standard 20 Schreibvorgänge pro Stunde, in den Optionen
  änderbar. Fängt Automationsschleifen ab.
* **Änderungshistorie** in `.storage`, überlebt Neustarts.
* **Undo** schreibt den alten Wert zurück und protokolliert das ebenfalls.
* **Snapshot-Wiederherstellung** legt vorher automatisch einen Snapshot des
  Ist-Zustands an — auch das Zurückrollen ist umkehrbar.
* **Ein Schreibvorgang zur Zeit**, abgesichert über einen Lock.

## Grenzen und Risiken

**Die Komponente greift auf Interna von `wolflink` zu** — `entry.runtime_data`,
`coordinator._wolf_client`, `coordinator._gateway_id`. Das ist keine öffentliche
Schnittstelle. Ein Home-Assistant-Update kann das brechen. Die Komponente steigt
in dem Fall mit einer Fehlermeldung aus, statt falsch zu schreiben.

**Die Smartset-Schnittstelle ist nicht für Fremdsoftware dokumentiert.** Es ist
Wolfs eigene Schnittstelle, dieselbe, die das Smartset-Portal und die App
benutzen — aber es gibt keine veröffentlichte Beschreibung und keine Zusage,
dass sie so bleibt. Ändert Wolf etwas daran, kann es sein, dass HeatWrite nicht
mehr läuft.

**Min und Max kommen aus der Rohbeschreibung.** Fehlen sie dort, bekommt die
`number`-Entität einen großzügigen Bereich nach Einheit. Die Regelung lehnt
unplausible Werte selbst ab, aber sie fängt nicht jeden Unsinn.

**Rückmeldung ist optimistisch.** Nach dem Schreiben zeigt die Entität sofort
den neuen Wert, obwohl die Wolf-Cloud ihn erst beim nächsten Abruf bestätigt.
Lehnt die Regelung den Wert ab, korrigiert sich die Anzeige beim nächsten Abruf.

**Manche Parameter lassen sich nicht lesen.** Die Wolf-API weist einzelne
Parameter beim Lesen ab und reißt damit den ganzen Bündelabruf mit. Die
Komponente fasst dann einzeln nach, merkt sich den Störenfried und lässt ihn
künftig aus — sichtbar als Warnung im Protokoll.

**Du änderst Parameter deiner Heizungsregelung.** Falsch gesetzte
Fachmann-Parameter können die Anlage ineffizient laufen lassen oder beschädigen.
Lege vor dem Experimentieren einen Snapshot an.

## Lizenz

MIT

## Tests

Ein Funktionstest der Schreiblogik mit Attrappen, ohne echte Wolf-Cloud:

```
python3 tests/smoke_test.py
```

Prüft Schreibvorgang und Nutzlast, No-Op-Erkennung, Schreibschutz, `force`,
`dry_run`, Übersetzung von Auswahlnamen in Zahlen, Undo, Snapshot,
Ratenbegrenzung und Fehlerprotokollierung.

## Symbol

Die Bilder liegen in `custom_components/heatwrite/brand/` und werden von Home
Assistant direkt von dort geladen — seit Version 2026.3 möglich, und lokale
Bilder haben Vorrang vor dem Repository
[home-assistant/brands](https://github.com/home-assistant/brands). Ein Pull
Request dorthin ist also nicht nötig.

Erzeugt mit `brand/make_brand.py`. Drei Regler auf einem Verlauf von Kühl-Blau
nach Heiz-Orange: einstellbare Parameter, die beiden Betriebsarten einer
Wärmepumpe. Eigene Gestaltung, keine fremden Markenzeichen, kein
Home-Assistant-Branding.

## Marken

WOLF und Smartset sind Kennzeichen der WOLF GmbH, Mainburg. HeatWrite steht in
keiner Verbindung zur WOLF GmbH und wird von ihr weder herausgegeben noch
unterstützt. Die Anbindung erfolgt ausschließlich über die Schnittstelle des
Wolf-Smartset-Dienstes, an dem die Anlage ohnehin angemeldet ist; die Namen
werden nur zur Bezeichnung dieser Schnittstelle und des unterstützten
Anlagentyps verwendet.

Home Assistant ist eine Marke der Open Home Foundation. Auch zu ihr besteht
keine Verbindung.

Alle weiteren genannten Produkt- und Firmennamen sind Marken oder eingetragene
Marken ihrer jeweiligen Inhaber.
