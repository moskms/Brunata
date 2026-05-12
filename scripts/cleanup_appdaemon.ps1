# cleanup_appdaemon.ps1
# Ryd lokal AppDaemon-struktur og genopret rene filer

$root = "p:\Brunata"
$adDir = "$root\appdaemon\apps\brunata"

Write-Host "Rydder gammel AppDaemon-struktur..." -ForegroundColor Yellow
Remove-Item -Recurse -Force "$root\appdaemon" -ErrorAction SilentlyContinue

Write-Host "Opretter ny mappe-struktur..." -ForegroundColor Yellow
New-Item -ItemType Directory -Force $adDir | Out-Null

Write-Host "Opretter brunata_app.py..." -ForegroundColor Cyan
@'
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
            attributes={"unit_of_measurement": "m³", "device_class": "water",
                        "state_class": "total_increasing", "friendly_name": "Brunata Varmt vand",
                        "last_updated": data.last_updated},
        )
        self.set_state(
            "sensor.brunata_cold_water_m3",
            state=data.cold_water_m3 if data.cold_water_m3 is not None else "unavailable",
            attributes={"unit_of_measurement": "m³", "device_class": "water",
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
            f"varmt vand={data.hot_water_m3} m³, "
            f"koldt vand={data.cold_water_m3} m³"
        )
'@ | Set-Content -Path "$adDir\brunata_app.py" -Encoding UTF8

Write-Host "Opretter apps.yaml..." -ForegroundColor Cyan
@'
hello_world:
  module: hello
  class: HelloWorld

brunata:
  module: brunata_app
  class: BrunataApp
  data_file: /config/apps/brunata/data/consumption.json
  update_interval: 300
'@ | Set-Content -Path "$adDir\apps.yaml" -Encoding UTF8

Write-Host ""
Write-Host "Lokal oprydning faerdig!" -ForegroundColor Green
Write-Host "Koer nu deploy.py i HA-terminalen:" -ForegroundColor Green
Write-Host "  python /config/deploy.py" -ForegroundColor White
