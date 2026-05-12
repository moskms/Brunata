import json
from pathlib import Path

import pytest

from brunata_client import BrunataClient
from brunata_client.models import ConsumptionData
from brunata_client.parser import parse_consumption_payload

FIXTURE_FILE = Path("data/consumption.json")


# ---------------------------------------------------------------------------
# BrunataClient
# ---------------------------------------------------------------------------

def test_client_instantiates():
    client = BrunataClient(username="test@example.com", password="secret")
    assert client.username == "test@example.com"
    assert client._access_token is None


def test_load_from_file_returns_consumption_data():
    data = BrunataClient.load_from_file(FIXTURE_FILE)
    assert isinstance(data, ConsumptionData)


def test_load_from_file_values():
    data = BrunataClient.load_from_file(FIXTURE_FILE)
    assert data.heat_kwh == pytest.approx(6087.882, rel=1e-3)
    assert data.hot_water_m3 == pytest.approx(151.204, rel=1e-3)
    assert data.cold_water_m3 == pytest.approx(167.439, rel=1e-3)
    assert data.last_updated is not None


def test_load_from_file_raw_meters():
    data = BrunataClient.load_from_file(FIXTURE_FILE)
    assert len(data.raw_meters) == 6
    allocation_units = {m.allocation_unit for m in data.raw_meters}
    assert "W" in allocation_units  # varmt vand
    assert "K" in allocation_units  # koldt vand
    assert "O" in allocation_units  # varme


def test_load_from_file_missing_file():
    from brunata_client.exceptions import BrunataDataError
    with pytest.raises(BrunataDataError):
        BrunataClient.load_from_file("data/does_not_exist.json")


# ---------------------------------------------------------------------------
# parse_consumption_payload
# ---------------------------------------------------------------------------

MINIMAL_PAYLOAD = {
    "heat_kwh": 100.0,
    "hot_water_m3": 10.5,
    "cold_water_m3": 20.0,
    "last_updated": "2026-01-01T00:00:00+01:00",
    "raw_meters": [
        {
            "meter_id": 1,
            "meter_no": "ABC",
            "placement": "Entre",
            "allocation_unit": "W",
            "unit": 8,
            "unit_label": "m³",
            "scale": None,
            "reading_value": 10.5,
            "reading_date": "2026-01-01T00:00:00+01:00",
            "transmitting": True,
        }
    ],
}


def test_parse_consumption_payload_top_level():
    data = parse_consumption_payload(MINIMAL_PAYLOAD)
    assert data.heat_kwh == 100.0
    assert data.hot_water_m3 == 10.5
    assert data.cold_water_m3 == 20.0
    assert data.last_updated == "2026-01-01T00:00:00+01:00"


def test_parse_consumption_payload_meter():
    data = parse_consumption_payload(MINIMAL_PAYLOAD)
    assert len(data.raw_meters) == 1
    m = data.raw_meters[0]
    assert m.meter_id == 1
    assert m.allocation_unit == "W"
    assert m.reading_value == 10.5
    assert m.unit_label == "m³"


def test_parse_consumption_payload_empty_meters():
    payload = {**MINIMAL_PAYLOAD, "raw_meters": []}
    data = parse_consumption_payload(payload)
    assert data.raw_meters == []


def test_parse_consumption_payload_missing_top_level_fields():
    data = parse_consumption_payload({})
    assert data.heat_kwh is None
    assert data.hot_water_m3 is None
    assert data.cold_water_m3 is None
    assert data.last_updated is None
    assert data.raw_meters == []


def test_parse_consumption_payload_matches_file():
    payload = json.loads(FIXTURE_FILE.read_text(encoding="utf-8"))
    data = parse_consumption_payload(payload)
    assert data.heat_kwh == pytest.approx(6087.882, rel=1e-3)
    assert data.hot_water_m3 == pytest.approx(151.204, rel=1e-3)
    assert data.cold_water_m3 == pytest.approx(167.439, rel=1e-3)
