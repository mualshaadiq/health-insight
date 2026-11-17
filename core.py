"""Shared request logic for the local server (server.py) and the Vercel function (api/data.py)."""

from __future__ import annotations

import hmac
import json
import os
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from garmin_client import DATASETS, GarminClient, GarminError, resolve_api_key

ROOT = Path(__file__).resolve().parent
ON_VERCEL = bool(os.environ.get("VERCEL"))
# Vercel's filesystem is read-only except /tmp
CACHE = Path(tempfile.gettempdir()) / "health-insight" if ON_VERCEL else ROOT / ".cache"
CACHE_TTL_S = 15 * 60


def today_in(tz: str | None) -> date:
    try:
        return datetime.now(ZoneInfo(tz)).date() if tz else date.today()
    except Exception:  # noqa: BLE001 - unknown zone name
        return date.today()


def fetch_all(client: GarminClient | None, days: int, demo: bool, tz: str | None = None) -> dict:
    end = today_in(tz)
    start = end - timedelta(days=days - 1)
    meta = {"start": start.isoformat(), "end": end.isoformat(), "days": days,
            "fetchedAt": datetime.now(ZoneInfo("UTC")).isoformat(timespec="seconds"), "demo": demo}
    if demo:
        from demo_data import generate
        return {"meta": meta, "sets": {k: {"ok": True, "data": v} for k, v in generate(start, end).items()}}

    assert client is not None
    client.token()  # fail fast (bad key / network) before fanning out

    def run(name):
        try:
            return name, {"ok": True, "data": DATASETS[name](client, start, end)}
        except GarminError as e:
            return name, {"ok": False, "error": str(e)}
        except Exception as e:  # noqa: BLE001 - one bad endpoint must not sink the dashboard
            return name, {"ok": False, "error": f"{type(e).__name__}: {e}"}

    with ThreadPoolExecutor(max_workers=8) as pool:
        sets = dict(pool.map(run, DATASETS))
    return {"meta": meta, "sets": sets}


def check_auth(authorization: str | None) -> tuple[bool, str | None]:
    """Password gate. Required on Vercel (fails closed); optional locally."""
    expected = os.environ.get("DASHBOARD_PASSWORD", "")
    if not expected:
        if ON_VERCEL:
            return False, "DASHBOARD_PASSWORD is not set on this deployment, so data access is disabled."
        return True, None
    given = (authorization or "").removeprefix("Bearer ").strip()
    if given and hmac.compare_digest(given.encode(), expected.encode()):
        return True, None
    time.sleep(0.5)  # slow down guessing
    return False, None


def handle_data(query: dict, authorization: str | None, demo: bool, client_factory) -> tuple[int, dict]:
    """Returns (status, payload) for GET /api/data."""
    ok, misconfig = check_auth(authorization)
    if misconfig:
        return 500, {"error": misconfig}
    if not ok:
        return 401, {"error": "Password required", "auth": True}
    try:
        days = max(7, min(365, int(query.get("days", ["90"])[0])))
    except ValueError:
        days = 90
    tz = query.get("tz", [None])[0]
    refresh = query.get("refresh", ["0"])[0] == "1"
    cache_file = CACHE / f"data-{'demo' if demo else 'live'}-{days}-{(tz or 'local').replace('/', '_')}.json"
    if not refresh and cache_file.is_file() and time.time() - cache_file.stat().st_mtime < CACHE_TTL_S:
        return 200, json.loads(cache_file.read_text())
    try:
        payload = fetch_all(None if demo else client_factory(), days, demo, tz)
    except GarminError as e:
        hint = ""
        if e.status in (401, 403):
            hint = " Check that your key is valid and not expired or revoked (Garmin Connect > Settings > Personal API)."
        return 502, {"error": str(e) + hint}
    CACHE.mkdir(parents=True, exist_ok=True)
    fd = os.open(cache_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump(payload, fh)
    return 200, payload


_client: GarminClient | None = None


def shared_client() -> GarminClient:
    """One client per process (reused across warm serverless invocations)."""
    global _client
    if _client is None:
        _client = GarminClient(resolve_api_key(), CACHE)
    return _client
