#!/usr/bin/env python3
"""
install_brunata.py — kør i HA-terminalen.
Opretter ALT det nødvendige direkte på HA uden manuel filkopiering.

Eneste krav: consumption.json skal kopieres separat til /homeassistant/consumption.json
Kørsel: python /homeassistant/install_brunata.py
"""
import shutil
import sys
from pathlib import Path

AD_APPS   = Path("/addon_configs/a0d7b954_appdaemon/apps")
APP_DIR   = AD_APPS / "brunata"
PKG_DIR   = APP_DIR / "brunata_client"
DATA_JSON = Path("/homeassistant/consumption.json")

# ---------------------------------------------------------------------------
# Check
# ---------------------------------------------------------------------------
print("\n=== Brunata install ===\n")

if not AD_APPS.exists():
    print("FEJL: AppDaemon-mappen findes ikke. Er AppDaemon installeret?")
    sys.exit(1)

if not DATA_JSON.exists():
    print(f"FEJL: {DATA_JSON} mangler.")
    print("Kopier data/consumption.json til /homeassistant/consumption.json via Total Commander.")
    sys.exit(1)

# ---------------------------------------------------------------------------
# Ryd gammel installation
# ---------------------------------------------------------------------------
shutil.rmtree(APP_DIR, ignore_errors=True)
APP_DIR.mkdir(parents=True)
PKG_DIR.mkdir()
(APP_DIR / "data").mkdir()
print("OK  mapper oprettet")

# ---------------------------------------------------------------------------
# brunata_client/__init__.py
# ---------------------------------------------------------------------------
(PKG_DIR / "__init__.py").write_text(
"""\
from .client import BrunataClient

__all__ = ["BrunataClient"]
""", encoding="utf-8")

# ---------------------------------------------------------------------------
# brunata_client/exceptions.py
# ---------------------------------------------------------------------------
(PKG_DIR / "exceptions.py").write_text(
"""\
class BrunataLoginError(Exception):
    \"\"\"Raised when login fails (wrong credentials, B2C error, etc.)\"\"\"


class BrunataDataError(Exception):
    \"\"\"Raised when consumption data cannot be fetched or parsed.\"\"\"


class BrunataSessionError(Exception):
    \"\"\"Raised when the session is invalid or the token has expired.\"\"\"
""", encoding="utf-8")

# ---------------------------------------------------------------------------
# brunata_client/models.py
# ---------------------------------------------------------------------------
(PKG_DIR / "models.py").write_text(
"""\
from dataclasses import dataclass, field


@dataclass
class MeterReading:
    meter_id: int
    meter_no: str
    placement: str
    allocation_unit: str
    unit: int
    unit_label: str
    scale: float | None
    reading_value: float | None
    reading_date: str | None
    transmitting: bool


@dataclass
class ConsumptionData:
    heat_kwh: float | None
    hot_water_m3: float | None
    cold_water_m3: float | None
    last_updated: str | None
    raw_meters: list[MeterReading] = field(default_factory=list)
""", encoding="utf-8")

# ---------------------------------------------------------------------------
# brunata_client/parser.py
# ---------------------------------------------------------------------------
(PKG_DIR / "parser.py").write_text(
"""\
from .models import ConsumptionData, MeterReading


def _meter_from_dict(m: dict) -> MeterReading:
    return MeterReading(
        meter_id=m["meter_id"],
        meter_no=m["meter_no"],
        placement=m["placement"],
        allocation_unit=m["allocation_unit"],
        unit=m["unit"],
        unit_label=m["unit_label"],
        scale=m.get("scale"),
        reading_value=m.get("reading_value"),
        reading_date=m.get("reading_date"),
        transmitting=m.get("transmitting", False),
    )


def parse_consumption_payload(payload: dict) -> ConsumptionData:
    return ConsumptionData(
        heat_kwh=payload.get("heat_kwh"),
        hot_water_m3=payload.get("hot_water_m3"),
        cold_water_m3=payload.get("cold_water_m3"),
        last_updated=payload.get("last_updated"),
        raw_meters=[_meter_from_dict(m) for m in payload.get("raw_meters", [])],
    )
""", encoding="utf-8")

# ---------------------------------------------------------------------------
# brunata_client/client.py
# ---------------------------------------------------------------------------
(PKG_DIR / "client.py").write_text(
"""\
import base64
import hashlib
import json
import os
import re
from pathlib import Path

from .exceptions import BrunataLoginError, BrunataDataError, BrunataSessionError
from .models import ConsumptionData
from .parser import parse_consumption_payload

_B2C_BASE      = "https://brunatab2cprod.b2clogin.com/brunatab2cprod.onmicrosoft.com/B2C_1_signin_username"
_AUTHORIZE_URL = f"{_B2C_BASE}/oauth2/v2.0/authorize"
_SELFASSERTED_URL = f"{_B2C_BASE}/SelfAsserted"
_CONFIRMED_URL = f"{_B2C_BASE}/api/CombinedSigninAndSignup/confirmed"
_TOKEN_URL     = "https://online.brunata.com/online-auth-webservice/v1/rest/oauth/token"
_CLIENT_ID     = "82770188-c92e-4d16-927d-a15c472eda55"
_SCOPE         = f"{_CLIENT_ID} offline_access"
_REDIRECT_URI  = "https://online.brunata.com/auth-redirect"


def _pkce_pair() -> tuple[str, str]:
    verifier  = base64.urlsafe_b64encode(os.urandom(32)).rstrip(b"=").decode()
    digest    = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


class BrunataClient:
    def __init__(self, username: str, password: str) -> None:
        import httpx  # lazy — not needed for load_from_file()
        self.username = username
        self.password = password
        self._client = httpx.AsyncClient(follow_redirects=False)
        self._access_token: str | None = None
        self._refresh_token: str | None = None

    @staticmethod
    def load_from_file(path: str | Path = "data/consumption.json") -> ConsumptionData:
        p = Path(path)
        if not p.exists():
            raise BrunataDataError(f"File not found: {p}")
        payload = json.loads(p.read_text(encoding="utf-8"))
        return parse_consumption_payload(payload)

    async def login(self) -> None:
        verifier, challenge = _pkce_pair()
        resp = await self._client.get(
            _AUTHORIZE_URL,
            params={"client_id": _CLIENT_ID, "response_type": "code", "scope": _SCOPE,
                    "redirect_uri": _REDIRECT_URI, "code_challenge": challenge,
                    "code_challenge_method": "S256"},
            follow_redirects=True,
        )
        csrf  = re.search(r'"csrf":"([^"]+)"', resp.text)
        trans = re.search(r'"transId":"([^"]+)"', resp.text)
        if not csrf or not trans:
            raise BrunataLoginError("Could not extract CSRF/transId from B2C authorize page")
        csrf_token, trans_id = csrf.group(1), trans.group(1)
        resp2 = await self._client.post(
            _SELFASSERTED_URL,
            params={"tx": trans_id, "p": "B2C_1_signin_username"},
            headers={"Content-Type": "application/x-www-form-urlencoded",
                     "X-CSRF-TOKEN": csrf_token,
                     "Referer": "https://brunatab2cprod.b2clogin.com/"},
            data={"request_type": "RESPONSE", "logonIdentifier": self.username,
                  "password": self.password},
        )
        body = resp2.json()
        if str(body.get("status")) != "200":
            raise BrunataLoginError(f"B2C login failed: {body.get('message', body)}")
        resp3    = await self._client.get(
            _CONFIRMED_URL,
            params={"csrf_token": csrf_token, "tx": trans_id, "p": "B2C_1_signin_username"},
        )
        location = resp3.headers.get("location", "")
        code     = re.search(r"[?&]code=([^&]+)", location)
        if not code:
            raise BrunataLoginError("No authorization code in B2C redirect")
        token_resp = await self._client.post(
            _TOKEN_URL,
            headers={"Content-Type": "application/x-www-form-urlencoded", "X-Skip-Interceptor": ""},
            data={"client_id": _CLIENT_ID, "scope": _SCOPE, "redirect_uri": _REDIRECT_URI,
                  "code": code.group(1), "grant_type": "authorization_code",
                  "code_verifier": verifier},
        )
        if token_resp.status_code != 200:
            raise BrunataLoginError(f"Token exchange failed: {token_resp.status_code}")
        tokens = token_resp.json()
        self._access_token  = tokens["access_token"]
        self._refresh_token = tokens.get("refresh_token")

    async def close(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "BrunataClient":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()
""", encoding="utf-8")

print("OK  brunata_client pakke oprettet")

# ---------------------------------------------------------------------------
# brunata_app.py
# ---------------------------------------------------------------------------
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
            self.log(f"Brunata: kunne ikke laese {self._data_file}: {exc}", level="ERROR")
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
""", encoding="utf-8")
print("OK  brunata_app.py oprettet")

# ---------------------------------------------------------------------------
# consumption.json
# ---------------------------------------------------------------------------
shutil.copy(DATA_JSON, APP_DIR / "data" / "consumption.json")
print("OK  consumption.json kopieret")

# ---------------------------------------------------------------------------
# apps.yaml
# ---------------------------------------------------------------------------
apps_yaml = AD_APPS / "apps.yaml"
existing = apps_yaml.read_text(encoding="utf-8").rstrip() if apps_yaml.exists() else ""
lines = existing.splitlines(keepends=True)
out, skip = [], False
for line in lines:
    if line.startswith("brunata:"):
        skip = True
    elif skip and (line.startswith(" ") or line.startswith("\t") or line.strip() == ""):
        continue
    else:
        skip = False
        out.append(line)
base = "".join(out).rstrip("\n") + "\n" if out else ""
apps_yaml.write_text(
    base + "\nbrunata:\n  module: brunata_app\n  class: BrunataApp\n"
    "  data_file: /config/apps/brunata/data/consumption.json\n"
    "  update_interval: 300\n",
    encoding="utf-8",
)
print("OK  apps.yaml opdateret")

# ---------------------------------------------------------------------------
# Resultat
# ---------------------------------------------------------------------------
print("\n=== Install faerdig! ===\n")
print("Oprettet:")
for p in sorted(APP_DIR.rglob("*")):
    indent = "  " * len(p.relative_to(APP_DIR).parts)
    print(f"{indent}{p.name}{'/' if p.is_dir() else ''}")
print()
print("Genstart AppDaemon:")
print("  ha apps restart a0d7b954_appdaemon")
