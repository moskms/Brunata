"""Manual smoke test against the REAL Brunata Online servers.

NOT part of the pytest suite (tests/ only uses offline fixtures) — this makes
real network calls with real credentials and is meant to be run by hand.

Usage:
    BRUNATA_USERNAME=you@example.com BRUNATA_PASSWORD=secret python scripts/live_smoke_test.py

Credentials are read from the environment only — never hardcode them here,
and never commit output containing tokens or account details.
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from brunata_client import BrunataClient
from brunata_client.exceptions import BrunataDataError, BrunataLoginError, BrunataSessionError


async def main() -> int:
    username = os.environ.get("BRUNATA_USERNAME")
    password = os.environ.get("BRUNATA_PASSWORD")
    if not username or not password:
        print(
            "ERROR: Set BRUNATA_USERNAME and BRUNATA_PASSWORD environment variables "
            "before running this script (never hardcode credentials in source).",
            file=sys.stderr,
        )
        return 1

    async with BrunataClient(username=username, password=password) as client:
        print("Logging in...")
        try:
            await client.login()
        except BrunataLoginError as exc:
            print(f"ERROR: login() failed — {exc}", file=sys.stderr)
            return 1
        except Exception as exc:  # unexpected transport/protocol failure
            print(
                f"ERROR: login() raised an unexpected {type(exc).__name__}: {exc}\n"
                "This usually means the Keycloak login flow (docs/login-flow.md) has "
                "changed on Brunata's side — a fresh HAR capture is probably needed.",
                file=sys.stderr,
            )
            return 1
        print("Login OK.")

        print("Fetching consumption data...")
        try:
            data = await client.fetch_consumption_data()
        except (BrunataDataError, BrunataSessionError) as exc:
            print(f"ERROR: fetch_consumption_data() failed — {exc}", file=sys.stderr)
            return 1
        except Exception as exc:  # unexpected payload/shape mismatch
            print(
                f"ERROR: fetch_consumption_data() raised an unexpected "
                f"{type(exc).__name__}: {exc}\n"
                "This usually means the live API response shape no longer matches "
                "docs/api-reference.md — inspect the raw response before changing code.",
                file=sys.stderr,
            )
            return 1

        print("\n--- Consumption data ---")
        print(f"heat_kwh:      {data.heat_kwh}")
        print(f"hot_water_m3:  {data.hot_water_m3}")
        print(f"cold_water_m3: {data.cold_water_m3}")
        print(f"last_updated:  {data.last_updated}")
        print(f"raw_meters:    {len(data.raw_meters)} meter(s)")
        return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
