"""Manual smoke test for historical meter-value import against the REAL
Brunata Online servers.

NOT part of the pytest suite — real network calls with real credentials,
meant to be run by hand to confirm the adaptive chunking logic in
brunata_client/history.py actually holds up against real data before
building statistics.py / the rest of custom_components/ on top of it.

Prints one line PER HTTP call as it happens (not just a summary at the end),
and flags anything that looks like rate-limiting/blocking: HTTP 429/403,
a response that's much slower than recent calls, or any single call over
30 seconds.

Usage:
    BRUNATA_USERNAME=you@example.com BRUNATA_PASSWORD=secret python scripts/history_smoke_test.py

Credentials are read from the environment only — never hardcode them here,
and never commit output containing tokens or account details.
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from brunata_client import BrunataClient
from brunata_client.exceptions import BrunataDataError, BrunataLoginError
from brunata_client.history import FetchAttempt, fetch_all_meter_history

# Not a confirmed rate-limit threshold — Brunata hasn't documented one. Purely
# a "this looks unusual, go check" heuristic.
_SLOW_CALL_SECONDS = 30.0
_SUSPICIOUS_SLOWDOWN_FACTOR = 3.0
_MIN_SAMPLES_BEFORE_FLAGGING_SLOWDOWN = 3
_SUSPICIOUS_STATUS_CODES = {429, 403}


class ProgressReporter:
    """Prints one line per HTTP call and flags anything that smells like
    rate-limiting/blocking, without assuming what the actual limit is.
    """

    def __init__(self) -> None:
        self._elapsed_history: list[float] = []

    def __call__(self, attempt: FetchAttempt) -> None:
        ts = attempt.timestamp.strftime("%H:%M:%S")
        interval = f"{attempt.start.isoformat()} -> {attempt.end.isoformat()}"

        if attempt.error is not None:
            print(
                f"[{ts}] meter={attempt.meter_id} interval={interval} "
                f"status={attempt.status_code} elapsed={attempt.elapsed_seconds:.2f}s "
                f"ERROR: {attempt.error}",
                flush=True,
            )
        else:
            print(
                f"[{ts}] meter={attempt.meter_id} interval={interval} "
                f"points={attempt.num_points} limited={attempt.limited} "
                f"status={attempt.status_code} elapsed={attempt.elapsed_seconds:.2f}s",
                flush=True,
            )

        self._flag_anomalies(attempt)
        self._elapsed_history.append(attempt.elapsed_seconds)

    def _flag_anomalies(self, attempt: FetchAttempt) -> None:
        if attempt.status_code in _SUSPICIOUS_STATUS_CODES:
            print(
                f"    !! WARNING: HTTP {attempt.status_code} on meter {attempt.meter_id} — "
                "this may mean Brunata is rate-limiting or blocking us. Consider stopping "
                "and retrying later.",
                flush=True,
            )

        if attempt.elapsed_seconds > _SLOW_CALL_SECONDS:
            print(
                f"    !! WARNING: call took {attempt.elapsed_seconds:.2f}s (> "
                f"{_SLOW_CALL_SECONDS:.0f}s) — unusually slow, not just waiting silently.",
                flush=True,
            )
        elif len(self._elapsed_history) >= _MIN_SAMPLES_BEFORE_FLAGGING_SLOWDOWN:
            avg = sum(self._elapsed_history) / len(self._elapsed_history)
            if avg > 0 and attempt.elapsed_seconds > avg * _SUSPICIOUS_SLOWDOWN_FACTOR:
                print(
                    f"    !! WARNING: this call ({attempt.elapsed_seconds:.2f}s) is "
                    f">{_SUSPICIOUS_SLOWDOWN_FACTOR:.0f}x slower than the running average "
                    f"({avg:.2f}s) — possibly suspicious, not confirmed as rate-limiting.",
                    flush=True,
                )


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
        print("Logging in...", flush=True)
        try:
            await client.login()
        except BrunataLoginError as exc:
            print(f"ERROR: login() failed — {exc}", file=sys.stderr)
            return 1
        print("Login OK.\n", flush=True)

        print("Fetching full history for heat/hot water/cold water meters...", flush=True)
        print("(this can take a while and make many API calls — that's expected)\n", flush=True)

        reporter = ProgressReporter()
        try:
            results = await fetch_all_meter_history(client, on_attempt=reporter)
        except BrunataDataError as exc:
            print(f"\nERROR: history import failed — {exc}", file=sys.stderr)
            return 1
        except Exception as exc:  # unexpected payload/shape mismatch
            print(
                f"\nERROR: history import raised an unexpected {type(exc).__name__}: {exc}\n"
                "This usually means the live API response shape no longer matches "
                "docs/api-reference.md — inspect the raw response before changing code.",
                file=sys.stderr,
            )
            return 1

        if not results:
            print(
                "WARNING: no meters with allocationUnit O/W/K were found in "
                "/consumer/metersforconsumer — nothing to report.",
                file=sys.stderr,
            )
            return 1

        total_calls = 0
        total_points = 0
        print(f"\n{'meter_id':>10}  {'points':>7}  {'api_calls':>9}  {'oldest':<25}  {'newest':<25}")
        for r in results:
            total_calls += r.api_calls
            total_points += len(r.points)
            if r.points:
                dates = [p["readingDate"] for p in r.points]
                oldest, newest = min(dates), max(dates)
            else:
                oldest = newest = "(no points)"
            print(f"{r.meter_id:>10}  {len(r.points):>7}  {r.api_calls:>9}  {oldest:<25}  {newest:<25}")

        print(f"\nTotal: {total_points} points, {total_calls} API calls across {len(results)} meter(s).")
        return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
