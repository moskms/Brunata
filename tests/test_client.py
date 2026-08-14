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
# BrunataClient.login() — exception classification
#
# Confirmed real case (2026-08-12): a transient 503 from Brunata's own
# /rest/authorize endpoint was previously mis-reported to the user as a
# wrong password, because every unexpected response anywhere in the 5-step
# login flow raised the same BrunataLoginError. These tests pin down the
# fix: only the ONE confirmed wrong-credentials case (the Keycloak login
# form re-rendering with an error) raises the more specific
# BrunataInvalidCredentialsError; everything else stays a plain
# BrunataLoginError.
# ---------------------------------------------------------------------------

def test_invalid_credentials_error_is_a_login_error():
    """Must stay a subclass — callers rely on `except BrunataLoginError`
    still catching this specific case too, just less precisely."""
    from brunata_client.exceptions import BrunataInvalidCredentialsError, BrunataLoginError
    assert issubclass(BrunataInvalidCredentialsError, BrunataLoginError)


def test_login_transient_server_error_raises_generic_login_error():
    import asyncio

    import httpx

    from brunata_client.exceptions import BrunataInvalidCredentialsError, BrunataLoginError

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    async def run():
        client = BrunataClient(username="test@example.com", password="secret")
        client._client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler), follow_redirects=False
        )
        try:
            with pytest.raises(BrunataLoginError) as exc_info:
                await client.login()
            assert not isinstance(exc_info.value, BrunataInvalidCredentialsError)
        finally:
            await client._client.aclose()

    asyncio.run(run())


def test_login_wrong_credentials_raises_invalid_credentials_error():
    import asyncio

    import httpx

    from brunata_client.exceptions import BrunataInvalidCredentialsError

    login_form_html = (
        '<html><body><form id="kc-form-login" '
        'action="https://fake.example/login-post"></form></body></html>'
    )

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if request.method == "GET" and "rest/authorize" in url:
            return httpx.Response(307, headers={"location": "https://fake.example/realm-auth"})
        if request.method == "GET" and "realm-auth" in url:
            return httpx.Response(200, text=login_form_html)
        if request.method == "POST" and "login-post" in url:
            return httpx.Response(200, text=login_form_html)  # re-rendered with an error
        raise AssertionError(f"Unexpected request in test: {request.method} {url}")

    async def run():
        client = BrunataClient(username="test@example.com", password="wrong")
        client._client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler), follow_redirects=False
        )
        try:
            with pytest.raises(BrunataInvalidCredentialsError):
                await client.login()
        finally:
            await client._client.aclose()

    asyncio.run(run())


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
