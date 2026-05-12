# Brunata Client

Uofficiel Python-klient til [Brunata Online](https://online.brunata.com) for beboere.
Henter forbrugsdata (varme, varmt vand, koldt vand) fra Brunata's portal.

> **Note:** Dette er en uofficiel klient og kan holde op med at virke hvis Brunata ændrer deres portal.

---

## Hvad gør dette projekt?

Projektet består af to dele:

**Del 1 — Python-klient (`brunata_client`)**
- Indlæser forbrugsdata fra en lokal `consumption.json`-fil (offline/test mode)
- Implementerer Azure AD B2C PKCE login-flow til fremtidig live-hentning
- Eksponerer data som Python-dataklasser (`ConsumptionData`, `MeterReading`)
- Kan køres fra kommandolinjen via `main.py`

**Del 2 — Home Assistant integration via AppDaemon**
- AppDaemon-app der publicerer Brunata-data som HA-sensorer
- Sensorer opdateres automatisk fra `consumption.json` (offline mode)
- Klar til live-opdatering når Del 2 Fase 2 implementeres

---

## Krav

- Python 3.12 eller nyere
- Pakker: se `pyproject.toml`
- Home Assistant med AppDaemon-addon (til HA-integration)

---

## Installation (Python-klient)

```bash
pip install -e ".[dev]"
```

---

## Opsætning

Opret en `.env` fil i projektmappen (se `.env.example`):

```env
BRUNATA_USERNAME=dit@email.dk
BRUNATA_PASSWORD=din_hemmelige_kode
```

---

## Kørsel

### Offline / test mode (ingen login)

Indlæs data fra en tidligere gemt `consumption.json`:

```bash
python main.py --file data/consumption.json
```

Kort opsummering i stedet for rå JSON:

```bash
python main.py --file data/consumption.json --summary
```

Gem output til en ny fil:

```bash
python main.py --file data/consumption.json --output data/output.json
```

### Live mode (kræver `.env` med credentials)

> **TODO / eksperimentel** — live mode er endnu ikke fuldt implementeret.

```bash
python main.py --live
```

---

## Eksempel på output

```json
{
  "heat_kwh": 6087.882,
  "hot_water_m3": 151.204,
  "cold_water_m3": 167.439,
  "last_updated": "2026-05-12T12:40:00+02:00",
  "raw_meters": [
    {
      "meter_id": 8260593,
      "meter_no": "60886237",
      "placement": "Entre",
      "allocation_unit": "W",
      "unit": 8,
      "unit_label": "m³",
      "scale": null,
      "reading_value": 151.204,
      "reading_date": "2026-05-12T11:38:00+02:00",
      "transmitting": true
    }
  ]
}
```

---

## Tests

```bash
pytest tests/ -v
```

Alle tests kører offline mod `data/consumption.json` — ingen credentials nødvendige.

---

## Projektstruktur

```
brunata_client/
├── src/brunata_client/
│   ├── __init__.py        # Eksporterer BrunataClient
│   ├── client.py          # BrunataClient: load_from_file(), login(), ...
│   ├── parser.py          # parse_consumption_payload()
│   ├── models.py          # ConsumptionData, MeterReading
│   └── exceptions.py      # BrunataLoginError, BrunataDataError, BrunataSessionError
├── appdaemon/
│   └── apps/brunata/
│       ├── brunata_app.py # AppDaemon-app til Home Assistant
│       └── apps.yaml      # AppDaemon konfiguration
├── scripts/
│   ├── install_brunata.py # Installationsscript til HA (kør i HA-terminal)
│   └── ha_cleanup.py      # Oprydningsscript til HA (kør i HA-terminal)
├── tests/
│   └── test_client.py
├── data/
│   └── consumption.json   # Seneste hentede data (ikke i git)
├── main.py
├── pyproject.toml
└── .env.example
```

---

## Home Assistant — AppDaemon integration

Brunata-data kan publiceres som sensorer i Home Assistant via AppDaemon.
Scriptet `scripts/install_brunata.py` håndterer hele installationen automatisk.

### Sensorer der oprettes

| Entity ID | Enhed | Beskrivelse |
|---|---|---|
| `sensor.brunata_heat_kwh` | kWh | Samlet varmeforbrug |
| `sensor.brunata_hot_water_m3` | m³ | Varmt vandforbrug |
| `sensor.brunata_cold_water_m3` | m³ | Koldt vandforbrug |
| `sensor.brunata_last_updated` | — | Tidspunkt for seneste måleraflæsning |

Opdateringsinterval: hvert 5. minut (konfigurerbart via `update_interval` i `apps.yaml`).

### Krav

- Home Assistant OS eller Supervised
- [AppDaemon-addon](https://github.com/hassio-addons/addon-appdaemon) installeret og startet
- En gyldig `consumption.json` fra Brunata

### Trin 1 — Forbered filer på Windows

Kør PowerShell-scriptet for at sikre lokale filer er korrekte:

```powershell
p:\Brunata\scripts\cleanup_appdaemon.ps1
```

### Trin 2 — Kopier til Home Assistant

Kopier følgende to filer til HA's `/config/`-mappe (f.eks. via Total Commander, Samba eller File Editor):

| Fil (Windows) | Destination (HA) |
|---|---|
| `scripts/install_brunata.py` | `/config/install_brunata.py` |
| `data/consumption.json` | `/config/consumption.json` |

### Trin 3 — Kør installationsscriptet

Åbn HA-terminalen (SSH-addon eller Terminal-addon) og kør:

```bash
python /homeassistant/install_brunata.py
```

Scriptet opretter automatisk:
```
/addon_configs/a0d7b954_appdaemon/apps/brunata/
├── brunata_app.py
├── brunata_client/          ← hele pakken kopieres hertil
│   ├── __init__.py
│   ├── client.py
│   ├── exceptions.py
│   ├── models.py
│   └── parser.py
└── data/
    └── consumption.json
```

og tilføjer `brunata`-sektionen til AppDaemon's `apps.yaml`.

### Trin 4 — Genstart AppDaemon

```bash
ha apps restart a0d7b954_appdaemon
```

### Trin 5 — Verificer

Gå til **Developer Tools → States** i HA og søg på `brunata`.
Alle 4 sensorer skal vises med aktuelle værdier.

### Oprydning

Hvis du vil fjerne alt Brunata-relateret fra HA igen:

```bash
# Kopier scripts/ha_cleanup.py til /config/ha_cleanup.py, derefter:
python /homeassistant/ha_cleanup.py
```

### Bemærkninger

- AppDaemon's `app_dir` er `/config/apps/` set fra AppDaemon's container, hvilket svarer til `/addon_configs/a0d7b954_appdaemon/apps/` på host-filsystemet
- Sensorernes `m³`-enhed vises korrekt i HA — terminal-visning kan vise tegnet forkert pga. encoding
- `httpx`-pakken er ikke nødvendig for offline mode og importeres derfor kun ved live login
