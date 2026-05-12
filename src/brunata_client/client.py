import base64
import hashlib
import json
import os
import re
from pathlib import Path

from .exceptions import BrunataLoginError, BrunataDataError, BrunataSessionError
from .models import ConsumptionData
from .parser import parse_consumption_payload

_B2C_BASE = (
    "https://brunatab2cprod.b2clogin.com"
    "/brunatab2cprod.onmicrosoft.com"
    "/B2C_1_signin_username"
)
_AUTHORIZE_URL = f"{_B2C_BASE}/oauth2/v2.0/authorize"
_SELFASSERTED_URL = f"{_B2C_BASE}/SelfAsserted"
_CONFIRMED_URL = f"{_B2C_BASE}/api/CombinedSigninAndSignup/confirmed"
_TOKEN_URL = "https://online.brunata.com/online-auth-webservice/v1/rest/oauth/token"
_API_BASE = "https://online.brunata.com/online-webservice/v2/rest"
_CLIENT_ID = "82770188-c92e-4d16-927d-a15c472eda55"
_SCOPE = f"{_CLIENT_ID} offline_access"
_REDIRECT_URI = "https://online.brunata.com/auth-redirect"


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
        self._client = httpx.AsyncClient(follow_redirects=False)
        self._access_token: str | None = None
        self._refresh_token: str | None = None

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
    # Live login flow (Azure AD B2C + PKCE)
    # ------------------------------------------------------------------

    async def login(self) -> None:
        """Full Azure AD B2C PKCE login flow."""
        verifier, challenge = _pkce_pair()

        resp = await self._client.get(
            _AUTHORIZE_URL,
            params={
                "client_id": _CLIENT_ID,
                "response_type": "code",
                "scope": _SCOPE,
                "redirect_uri": _REDIRECT_URI,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            },
            follow_redirects=True,
        )
        csrf = re.search(r'"csrf":"([^"]+)"', resp.text)
        trans = re.search(r'"transId":"([^"]+)"', resp.text)
        if not csrf or not trans:
            raise BrunataLoginError("Could not extract CSRF/transId from B2C authorize page")

        csrf_token = csrf.group(1)
        trans_id = trans.group(1)

        resp2 = await self._client.post(
            _SELFASSERTED_URL,
            params={"tx": trans_id, "p": "B2C_1_signin_username"},
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "X-CSRF-TOKEN": csrf_token,
                "Referer": "https://brunatab2cprod.b2clogin.com/",
            },
            data={
                "request_type": "RESPONSE",
                "logonIdentifier": self.username,
                "password": self.password,
            },
        )
        body = resp2.json()
        if str(body.get("status")) != "200":
            raise BrunataLoginError(f"B2C login failed: {body.get('message', body)}")

        resp3 = await self._client.get(
            _CONFIRMED_URL,
            params={
                "csrf_token": csrf_token,
                "tx": trans_id,
                "p": "B2C_1_signin_username",
            },
        )
        location = resp3.headers.get("location", "")
        code_match = re.search(r"[?&]code=([^&]+)", location)
        if not code_match:
            raise BrunataLoginError("No authorization code in B2C redirect")

        token_resp = await self._client.post(
            _TOKEN_URL,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "X-Skip-Interceptor": "",
            },
            data={
                "client_id": _CLIENT_ID,
                "scope": _SCOPE,
                "redirect_uri": _REDIRECT_URI,
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
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "X-Skip-Interceptor": "",
            },
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
    # Live data fetch — TODO
    # ------------------------------------------------------------------

    async def fetch_consumption_data(self) -> ConsumptionData:
        """Fetch live meter readings from Brunata API (requires login()).

        TODO: implement live fetch when endpoint is fully verified.
        Use load_from_file() for offline/test mode in the meantime.
        """
        raise NotImplementedError(
            "Live fetch not yet implemented. Use load_from_file() instead."
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "BrunataClient":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()
