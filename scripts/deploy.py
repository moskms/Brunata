#!/usr/bin/env python3
"""
deploy.py — kør i HA-terminalen for at installere Brunata AppDaemon-app.

Placering: kopier til /root/deploy.py eller /homeassistant/deploy.py
Kørsel:    python /homeassistant/deploy.py
"""
import shutil
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Konfiguration
# ---------------------------------------------------------------------------

AD_APPS   = Path("/addon_configs/a0d7b954_appdaemon/apps")
APP_DIR   = AD_APPS / "brunata"
SRC_PKG   = Path("/homeassistant/apps/brunata_client")
SRC_DATA  = Path("/homeassistant/apps/data/consumption.json")
APPS_YAML = AD_APPS / "apps.yaml"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def info(msg: str) -> None:
    print(f"  {msg}")

def ok(msg: str) -> None:
    print(f"  OK  {msg}")

def err(msg: str) -> None:
    print(f"  FEJL  {msg}", file=sys.stderr)

# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------

print("\n=== Brunata AppDaemon deploy ===\n")

if not AD_APPS.exists():
    err(f"AppDaemon-mappen findes ikke: {AD_APPS}")
    err("Er AppDaemon installeret og har kørt mindst én gang?")
    sys.exit(1)

if not SRC_PKG.exists():
    err(f"brunata_client-pakken findes ikke: {SRC_PKG}")
    err("Kopier src/brunata_client/ til /homeassistant/apps/brunata_client/ først.")
    sys.exit(1)

if not SRC_DATA.exists():
    err(f"consumption.json findes ikke: {SRC_DATA}")
    err("Kopier data/consumption.json til /homeassistant/apps/data/consumption.json først.")
    sys.exit(1)

# ---------------------------------------------------------------------------
# Ryd gammel app-mappe
# ---------------------------------------------------------------------------

info(f"Rydder {APP_DIR} ...")
shutil.rmtree(APP_DIR, ignore_errors=True)
APP_DIR.mkdir(parents=True)
ok("App-mappe ryddet og genoprettet")

# ---------------------------------------------------------------------------
# brunata_app.py
# ---------------------------------------------------------------------------

info("Opretter brunata_app.py ...")
(APP_DIR / "brunata_app.py").write_text(
    """\
from pathlib import Path

import hassapi as hass

from brunata_client import BrunataClient
from brunata_client.exceptions import BrunataDataError


class BrunataApp(hass.Hass):

    def initialize(self) -> None:
        self._data_file = Path(self.args.get("data_file", "data/consumption.json"))
        interval = int(self.args.get("update_interval", 300))
        self._publish()
        self.run_every(self._on_interval, "now+1", interval)

    def _on_interval(self, kwargs: dict) -> None:
        self._publish()

    def _publish(self) -> None:
        try:
            data = BrunataClient.load_from_file(self._data_file)
        except BrunataDataError as exc:
            self.log(f"Brunata: kunne ikke læse {self._data_file}: {exc}", level="ERROR")
            return

        self.set_state(
            "sensor.brunata_heat_kwh",
            state=data.heat_kwh if data.heat_kwh is not None else "unavailable",
            attributes={"unit_of_measurement": "kWh", "device_class": "energy",
                        "state_class": "total_increasing", "friendly_name": "Brunata Varme",
                        "last_updated": data.last_updated},
        )
        self.set_state(
            "sensor.brunata_hot_water_m3",
            state=data.hot_water_m3 if data.hot_water_m3 is not None else "unavailable",
            attributes={"unit_of_measurement": "m\\u00b3", "device_class": "water",
                        "state_class": "total_increasing", "friendly_name": "Brunata Varmt vand",
                        "last_updated": data.last_updated},
        )
        self.set_state(
            "sensor.brunata_cold_water_m3",
            state=data.cold_water_m3 if data.cold_water_m3 is not None else "unavailable",
            attributes={"unit_of_measurement": "m\\u00b3", "device_class": "water",
                        "state_class": "total_increasing", "friendly_name": "Brunata Koldt vand",
                        "last_updated": data.last_updated},
        )
        self.set_state(
            "sensor.brunata_last_updated",
            state=data.last_updated if data.last_updated is not None else "unavailable",
            attributes={"friendly_name": "Brunata Senest opdateret"},
        )
        self.log(
            f"Brunata: varme={data.heat_kwh} kWh, "
            f"varmt vand={data.hot_water_m3} m\\u00b3, "
            f"koldt vand={data.cold_water_m3} m\\u00b3"
        )
""",
    encoding="utf-8",
)
ok("brunata_app.py oprettet")

# ---------------------------------------------------------------------------
# brunata_client-pakke
# ---------------------------------------------------------------------------

info("Kopierer brunata_client ...")
shutil.copytree(SRC_PKG, APP_DIR / "brunata_client")
ok("brunata_client kopieret")

# ---------------------------------------------------------------------------
# consumption.json
# ---------------------------------------------------------------------------

info("Kopierer consumption.json ...")
data_dir = APP_DIR / "data"
data_dir.mkdir()
shutil.copy(SRC_DATA, data_dir / "consumption.json")
ok("consumption.json kopieret")

# ---------------------------------------------------------------------------
# apps.yaml — bevar hello_world, erstat brunata-sektionen
# ---------------------------------------------------------------------------

info("Opdaterer apps.yaml ...")
brunata_block = """\
brunata:
  module: brunata_app
  class: BrunataApp
  data_file: /config/apps/brunata/data/consumption.json
  update_interval: 300
"""

if APPS_YAML.exists():
    existing = APPS_YAML.read_text(encoding="utf-8")
    # Fjern gammel brunata-sektion hvis den findes
    lines = existing.splitlines(keepends=True)
    out, skip = [], False
    for line in lines:
        if line.startswith("brunata:"):
            skip = True
        elif skip and (line.startswith(" ") or line.startswith("\t")):
            continue
        else:
            skip = False
            out.append(line)
    base = "".join(out).rstrip("\n") + "\n"
else:
    base = ""

APPS_YAML.write_text(base + "\n" + brunata_block, encoding="utf-8")
ok("apps.yaml opdateret")

# ---------------------------------------------------------------------------
# Resultat
# ---------------------------------------------------------------------------

print("\n=== Deploy færdig! ===")
print()
print("Mappe-struktur:")
for p in sorted(APP_DIR.rglob("*")):
    indent = "  " * (len(p.relative_to(APP_DIR).parts) - 1)
    print(f"  {indent}{p.name}{'/' if p.is_dir() else ''}")
print()
print("Genstart AppDaemon:")
print("  ha apps restart a0d7b954_appdaemon")
print()
