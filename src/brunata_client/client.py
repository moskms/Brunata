import base64
import hashlib
import json
import os
import re
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urljoin

from .exceptions import BrunataLoginError, BrunataDataError, BrunataHttpError, BrunataSessionError
from .models import ConsumptionData, MeterReading
from .parser import parse_consumption_payload

# Keycloak Authorization Code + PKCE flow — see docs/login-flow.md.
# NOT Azure AD B2C, despite what older comments in this file used to say.
_AUTHORIZE_URL = "https://online.brunata.com/online-auth-webservice/v1/rest/authorize"
_TOKEN_URL = "https://online.brunata.com/online-auth-webservice/v1/rest/oauth/token"
_API_BASE = "https://online.brunata.com/online-webservice/v2/rest"
_CLIENT_ID = "82770188-c92e-4d16-927d-a15c472eda55"
_SCOPE = "openid profile email"
_REDIRECT_URI = "https://online.brunata.com/auth-redirect"

# unit code -> display label. 8=m3, 1=enheder (pulses), 7=kWh.
_UNIT_LABELS = {8: "m³", 1: "enheder", 7: "kWh"}


def _extract_login_form_action(html: str) -> str | None:
    """Extract the `action` attribute of the Keycloak login <form>."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "lxml")
    form = soup.find("form", id="kc-form-login") or soup.find("form")
    if form is None:
        return None
    action = form.get("action")
    return str(action) if action else None


def _format_brunata_datetime(dt: datetime) -> str:
    """Format a datetime as Brunata expects: 2026-06-12T00:00:00.000+02:00."""
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}" + _offset_str(dt)


def _offset_str(dt: datetime) -> str:
    offset = dt.utcoffset() or timedelta(0)
    total_minutes = int(offset.total_seconds() // 60)
    sign = "+" if total_minutes >= 0 else "-"
    total_minutes = abs(total_minutes)
    return f"{sign}{total_minutes // 60:02d}:{total_minutes % 60:02d}"


def _pkce_pair() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(os.urandom(32)).rstrip(b"=").decode()
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


class BrunataClient:
    def __init__(self, username: str, password: str) -> None:
        import httpx  # lazy — not needed for load_from_file()
        self.username = username
        self.password = password
        # One persistent client/cookie-jar for the whole login chain AND all
        # subsequent API calls — see "Kritisk implementeringsdetalje" in
        # docs/login-flow.md. Redirects are followed manually so we can read
        # `code` out of intermediate Location headers.
        self._client = httpx.AsyncClient(follow_redirects=False)
        self._access_token: str | None = None
        self._refresh_token: str | None = None
        # meterId -> scale, sourced from /consumer/consumption (see fetch_consumption_data).
        self._meter_scale_cache: dict[int, float] = {}

    # ------------------------------------------------------------------
    # Offline / test mode
    # ------------------------------------------------------------------

    @staticmethod
    def load_from_file(path: str | Path = "data/consumption.json") -> ConsumptionData:
        """Load ConsumptionData from a saved consumption.json (no login required)."""
        p = Path(path)
        if not p.exists():
            raise BrunataDataError(f"File not found: {p}")
        payload = json.loads(p.read_text(encoding="utf-8"))
        return parse_consumption_payload(payload)

    # ------------------------------------------------------------------
    # Live login flow (Keycloak Authorization Code + PKCE)
    # ------------------------------------------------------------------

    async def login(self) -> None:
        """Full Keycloak Authorization Code + PKCE login flow.

        See docs/login-flow.md for the confirmed, HAR-captured 5-step flow.
        """
        verifier, challenge = _pkce_pair()
        authorize_params = {
            "client_id": _CLIENT_ID,
            "redirect_uri": _REDIRECT_URI,
            "scope": _SCOPE,
            "response_type": "code",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }

        # Step 1: GET .../rest/authorize -> 307 redirect to the Keycloak realm.
        resp1 = await self._client.get(_AUTHORIZE_URL, params=authorize_params)
        if resp1.status_code != 307 or "location" not in resp1.headers:
            raise BrunataLoginError(
                f"Unexpected response from authorize endpoint: {resp1.status_code}"
            )
        realm_auth_url = urljoin(str(resp1.url), resp1.headers["location"])

        # Step 2: GET the Keycloak realm auth URL -> HTML login page with a
        # form containing one-time session_code/execution/tab_id/client_data.
        resp2 = await self._client.get(realm_auth_url)
        if resp2.status_code != 200:
            raise BrunataLoginError(
                f"Could not load Keycloak login page: {resp2.status_code}"
            )
        form_action = _extract_login_form_action(resp2.text)
        if not form_action:
            raise BrunataLoginError(
                "Could not find login form action on Keycloak login page"
            )
        form_action = urljoin(str(resp2.url), form_action)

        # Step 3: POST credentials to the form action.
        resp3 = await self._client.post(
            form_action,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={
                "username": self.username,
                "password": self.password,
                "credentialId": "",
            },
        )
        if resp3.status_code == 200:
            # Login page re-rendered with an error message, per docs/login-flow.md.
            raise BrunataLoginError("Login failed: wrong username or password")
        if resp3.status_code != 302 or "location" not in resp3.headers:
            raise BrunataLoginError(
                f"Unexpected response after login POST: {resp3.status_code}"
            )
        redirect_location = urljoin(str(resp3.url), resp3.headers["location"])
        code_match = re.search(r"[?&]code=([^&]+)", redirect_location)
        if not code_match:
            raise BrunataLoginError("No authorization code in Keycloak redirect")

        # Step 4: GET the auth-redirect URL from step 3 (keeps the session
        # cookie chain consistent). The authorization code was already parsed
        # out of the URL itself above.
        resp4 = await self._client.get(redirect_location)
        if resp4.status_code != 200:
            raise BrunataLoginError(
                f"Unexpected response from auth-redirect: {resp4.status_code}"
            )

        # Step 5: exchange the authorization code for tokens.
        token_resp = await self._client.post(
            _TOKEN_URL,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={
                "client_id": _CLIENT_ID,
                "redirect_uri": _REDIRECT_URI,
                "scope": _SCOPE,
                "code": code_match.group(1),
                "grant_type": "authorization_code",
                "code_verifier": verifier,
            },
        )
        if token_resp.status_code != 200:
            raise BrunataLoginError(
                f"Token exchange failed: {token_resp.status_code} {token_resp.text}"
            )
        tokens = token_resp.json()
        self._access_token = tokens["access_token"]
        self._refresh_token = tokens.get("refresh_token")

    async def refresh_login(self) -> None:
        """Use refresh_token to obtain a new access_token."""
        if not self._refresh_token:
            raise BrunataSessionError("No refresh token — call login() first")
        resp = await self._client.post(
            _TOKEN_URL,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={
                "client_id": _CLIENT_ID,
                "scope": _SCOPE,
                "refresh_token": self._refresh_token,
                "grant_type": "refresh_token",
            },
        )
        if resp.status_code != 200:
            raise BrunataSessionError(
                f"Token refresh failed: {resp.status_code} {resp.text}"
            )
        tokens = resp.json()
        self._access_token = tokens["access_token"]
        if "refresh_token" in tokens:
            self._refresh_token = tokens["refresh_token"]

    # ------------------------------------------------------------------
    # Live data fetch
    # ------------------------------------------------------------------

    async def _authed_get(self, url: str, params: dict | None = None) -> dict | list:
        """GET an API endpoint using the shared session cookie.

        docs/login-flow.md: no Authorization header was observed in the HAR
        capture, so the cookie set during login is tried first. If that 401s,
        an `Authorization: Bearer <access_token>` fallback is tried next (per
        docs). If both fail, the token is refreshed (or a full re-login is
        performed) and the request is retried once more.
        """
        resp = await self._client.get(url, params=params)
        if resp.status_code == 401 and self._access_token:
            resp = await self._client.get(
                url, params=params,
                headers={"Authorization": f"Bearer {self._access_token}"},
            )
        if resp.status_code == 401:
            try:
                await self.refresh_login()
            except BrunataSessionError:
                await self.login()
            resp = await self._client.get(
                url, params=params,
                headers={"Authorization": f"Bearer {self._access_token}"},
            )
        if resp.status_code != 200:
            raise BrunataHttpError(
                resp.status_code,
                f"API request to {url} failed: {resp.status_code} {resp.text}",
            )
        return resp.json()

    async def fetch_meters_for_consumer(self) -> list[dict]:
        """GET /consumer/metersforconsumer — raw metadata for every meter the
        consumer has ever had, including dismounted ones (`dismountedDate` set).
        """
        return await self._authed_get(f"{_API_BASE}/consumer/metersforconsumer")

    async def fetch_meter_values(self, meter_id: int, start: datetime, end: datetime) -> dict:
        """GET /consumer/meters/{meterId}/metervalues for the [start, end) window.

        Returns {"meterValues": [...], "limited": bool}. Per docs/api-reference.md
        this endpoint has no pagination: if the window contains more than 600
        readings, only the 600 newest are returned and `limited` is True — the
        caller is responsible for shrinking the window and retrying rather than
        treating a limited response as complete.
        """
        return await self._authed_get(
            f"{_API_BASE}/consumer/meters/{meter_id}/metervalues",
            params={
                "startdate": _format_brunata_datetime(start),
                "enddate": _format_brunata_datetime(end),
            },
        )

    async def _fetch_transmitting_by_meter_id(self) -> dict[int, bool]:
        """GET /consumer/metersforconsumer -> {meterId: transmitting}."""
        meters = await self.fetch_meters_for_consumer()
        return {m["meterId"]: m["transmitting"] for m in meters}

    async def _fetch_scale(self, meter_id: int, allocation_unit: str) -> float | None:
        """Get the scale/multiplier for a meter from /consumer/consumption.

        docs/api-reference.md's /consumer/meteroverview has no `scale` field.
        Per confirmed HAR capture, the multiplier is only present on the
        `meter` object nested inside /consumer/consumption responses. A short
        one-day window is enough since only the meter metadata is needed, not
        the consumption values. Cached per meter_id since scale is static.
        """
        if meter_id in self._meter_scale_cache:
            return self._meter_scale_cache[meter_id]

        now = datetime.now().astimezone()
        payload = await self._authed_get(
            f"{_API_BASE}/consumer/consumption",
            params={
                "startdate": _format_brunata_datetime(now - timedelta(days=1)),
                "enddate": _format_brunata_datetime(now),
                "interval": "D",
                "allocationunit": allocation_unit,
            },
        )
        for line in payload.get("consumptionLines", []):
            meter = line.get("meter", {})
            if meter.get("meterId") == meter_id:
                scale = meter.get("scale")
                self._meter_scale_cache[meter_id] = scale
                return scale
        return None

    async def fetch_consumption_data(self) -> ConsumptionData:
        """Fetch live meter readings from Brunata API (requires login()).

        Uses GET /consumer/meteroverview (docs/api-reference.md) for the
        current snapshot per meter, GET /consumer/metersforconsumer for the
        `transmitting` flag (not present in meteroverview), and — only for
        heat (allocationUnit "O") meters — GET /consumer/consumption to look
        up the `scale` multiplier needed to convert the raw reading to kWh.
        """
        overview = await self._authed_get(f"{_API_BASE}/consumer/meteroverview")
        transmitting_by_id = await self._fetch_transmitting_by_meter_id()

        raw_meters: list[MeterReading] = []
        totals: dict[str, float] = {}
        last_updated: str | None = None

        for m in overview:
            meter_id = m["meterId"]
            allocation_unit = m["alloUnitType"]
            unit = m["unit"]
            reading_value = m.get("meterValue")
            reading_date = m.get("telegramDate")

            scale: float | None = None
            if allocation_unit == "O" and reading_value is not None:
                scale = await self._fetch_scale(meter_id, allocation_unit)

            raw_meters.append(
                MeterReading(
                    meter_id=meter_id,
                    meter_no=m["meterNo"],
                    placement=m["placement"],
                    allocation_unit=allocation_unit,
                    unit=unit,
                    unit_label=_UNIT_LABELS.get(unit, str(unit)),
                    scale=scale,
                    reading_value=reading_value,
                    reading_date=reading_date,
                    transmitting=transmitting_by_id.get(meter_id, False),
                )
            )

            if reading_value is not None:
                value = reading_value * scale if scale is not None else reading_value
                totals[allocation_unit] = totals.get(allocation_unit, 0.0) + value
                if reading_date is not None and (
                    last_updated is None or reading_date > last_updated
                ):
                    last_updated = reading_date

        return ConsumptionData(
            heat_kwh=totals.get("O"),
            hot_water_m3=totals.get("W"),
            cold_water_m3=totals.get("K"),
            last_updated=last_updated,
            raw_meters=raw_meters,
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "BrunataClient":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()
