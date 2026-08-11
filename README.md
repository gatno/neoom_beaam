# neoom BEAAM für Home Assistant

[![hacs][hacs-badge]][hacs-url]
[![release][release-badge]][release-url]
![inoffiziell][unofficial-badge]

> [!IMPORTANT]
> **Inoffizielles Projekt.** Diese Integration ist ein privates Community-Projekt und
> steht in **keiner Verbindung zur neoom AG**. Sie wird von neoom weder entwickelt noch
> geprüft, unterstützt oder freigegeben. Es besteht kein Anspruch auf Support, und für
> Schäden oder Fehlfunktionen an deiner Anlage wird keine Haftung übernommen — die
> Nutzung erfolgt auf eigenes Risiko. Wende dich mit Problemen **nicht an den
> neoom-Support**, sondern an die [Issues][issues] dieses Repositories.
>
> Über die `number`- und `select`-Entitäten werden Einstellungen **in dein
> Energiemanagement zurückgeschrieben** (z. B. Min. SOC, Ladeströme, Betriebsmodus).
> Sei dir bewusst, was du damit veränderst.

Custom Integration für das [neoom BEAAM][beaam] Energiemanagement-System. Die Anbindung
läuft vollständig **lokal** über die REST-API des Geräts — keine Cloud, kein neoom-Konto
zur Laufzeit nötig.

Alle Things (PV, Wechselrichter, Batterie, Stromzähler, Ladepunkte, Wärmepumpe) werden
automatisch erkannt und als eigene Geräte angelegt.

## Funktionen

| Plattform | Beispiele |
|---|---|
| `sensor` | PV-Leistung, Verbrauch, Netzbezug/-einspeisung, Batterie laden/entladen, Ladestand, Autarkiegrad, Energiezähler (Wh), Energiefluss-Anteile, pro Thing: Spannung/Strom/Leistung je Phase, Frequenz, Zählerstände, SOC, Batteriegesundheit |
| `binary_sensor` | Erzeuger/Speicher/Stromzähler/Ladepunkte/Heizung online, Verbunden, Notstrom aktiv |
| `number` | Min. SOC (Eigenverbrauch/Notstrom), Max. Lade-/Entladestrom, Zielleistung, Wirkleistungsbegrenzung, Max. Einspeisung |
| `select` | Betriebsmodus, Controller-Modus |

Die Energiezähler nutzen `state_class: total_increasing` und lassen sich direkt im
**Energie-Dashboard** von Home Assistant verwenden.

## Installation

### Über HACS (empfohlen)

1. HACS öffnen → **Integrationen** → Menü oben rechts → **Benutzerdefinierte Repositories**
2. Repository `https://github.com/gatno/neoom_beaam` hinzufügen, Kategorie **Integration**
3. „neoom BEAAM" suchen, **Herunterladen**
4. Home Assistant neu starten

### Manuell

Den Ordner `custom_components/neoom_beaam` in das `config/custom_components/`-Verzeichnis
kopieren und Home Assistant neu starten.

## Einrichtung

**Einstellungen → Geräte & Dienste → Integration hinzufügen → „neoom BEAAM"**

| Feld | Beschreibung |
|---|---|
| Host | IP-Adresse oder Hostname der BEAAM, z. B. `192.168.1.50` |
| API-Key | Zugriffstoken der BEAAM (siehe unten) |
| Abfrageintervall | Sekunden zwischen zwei Abfragen, Standard `10` |

### API-Key erzeugen

Unter [connect.neoom.com][neoom-connect] → **API Schlüssel** → **Schlüssel für BEAAM API**
einen Token anlegen. Details in der [neoom Entwicklerdokumentation][api-docs].

Zum Prüfen:

```bash
curl -H "Authorization: Bearer DEIN_API_KEY" http://192.168.1.50/api/v1/site/state
```

## Konfiguration ändern

Über **Konfigurieren** an der Integrationskarte lässt sich das Abfrageintervall
nachträglich anpassen. Ein Intervall unter 10 Sekunden belastet das Gerät spürbar und
bringt selten zusätzlichen Nutzen.

Hat die BEAAM eine neue IP-Adresse bekommen, führt **Neu konfigurieren** im Menü der
Integrationskarte zum Ziel — Entitäten und Statistik-Historie bleiben erhalten. Wird der
API-Schlüssel ungültig, meldet Home Assistant das von selbst und fragt über **Neu
authentifizieren** einen neuen ab.

Neu hinzugefügte Things erkennt die Integration im laufenden Betrieb; die
Standortkonfiguration wird regelmäßig neu eingelesen.

## Verhalten bei Störungen

Die BEAAM ist ein kleines Embedded-Gerät und antwortet gelegentlich unvollständig oder
gar nicht. Die Integration federt das ab:

* Fehlgeschlagene HTTP-Requests werden bis zu dreimal wiederholt (die BEAAM schließt
  ungenutzte Keep-Alive-Verbindungen still).
* Erst nach drei aufeinanderfolgenden Fehlversuchen gehen die Entitäten auf
  `unavailable` — ein einzelner Aussetzer leert nicht das ganze Dashboard.
* Datenpunkte, die in einer Antwort fehlen oder `null` sind, behalten ihren letzten
  bekannten Wert, statt auf `unknown` zu fallen.
* Bei `total_increasing`-Zählern werden minimale Rückschritte (≤ 2 %, entstehen durch
  interne Neuberechnung in der BEAAM) geglättet, damit die Langzeitstatistik sie nicht
  als Zählerreset wertet.

> **Hinweis für eigene Template-Sensoren:** Beim Neustart von Home Assistant sind alle
> Entitäten kurzzeitig `unavailable`. Nutze in Templates immer einen Default:
> `{{ states('sensor.neoom_beaam_netzbezug') | float(0) }}`

## Fehlersuche

Debug-Logging in der `configuration.yaml` aktivieren:

```yaml
logger:
  default: warning
  logs:
    custom_components.neoom_beaam: debug
```

| Symptom | Mögliche Ursache |
|---|---|
| Einrichtung schlägt mit Authentifizierungsfehler fehl | API-Key falsch oder abgelaufen |
| Einrichtung meldet, das Gerät sei nicht erreichbar | Falsche IP, oder HA und BEAAM in getrennten VLANs |
| Einzelne Sensoren fehlen | Die BEAAM meldet den Datenpunkt (noch) nicht — sie erscheinen automatisch, sobald er auftaucht |

## Mitwirken

Fehlerberichte und Pull Requests sind willkommen — bitte über die
[Issues][issues] des Repositories.

## Lizenz

MIT — siehe [LICENSE](LICENSE).

## Rechtliches

Dieses Projekt steht in keiner Verbindung zur **neoom AG** und wird von ihr weder
entwickelt, geprüft, unterstützt noch freigegeben. „neoom" und „BEAAM" sind Marken der
neoom AG und werden hier ausschließlich zur Beschreibung der Kompatibilität verwendet.

Die Software wird ohne jede Gewährleistung bereitgestellt (siehe [LICENSE](LICENSE)).
Die Nutzung — insbesondere das Schreiben von Einstellungen an die BEAAM — erfolgt auf
eigenes Risiko. Prüfe vorab, ob Eingriffe über die lokale API mit deinen Garantie- oder
Förderbedingungen vereinbar sind.

[beaam]: https://neoom.com
[neoom-connect]: https://connect.neoom.com
[api-docs]: https://developer.neoom.com/reference/interacting-with-your-beaam
[issues]: https://github.com/gatno/neoom_beaam/issues
[unofficial-badge]: https://img.shields.io/badge/nicht%20offiziell-kein%20neoom%20Projekt-orange
[hacs-badge]: https://img.shields.io/badge/HACS-Custom-41BDF5.svg
[hacs-url]: https://github.com/hacs/integration
[release-badge]: https://img.shields.io/github/v/release/gatno/neoom_beaam
[release-url]: https://github.com/gatno/neoom_beaam/releases
