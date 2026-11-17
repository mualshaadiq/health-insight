"""Shared request logic for the local server (server.py) and the Vercel functions (api/*.py)."""

from __future__ import annotations

import hmac
import json
import os
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import analysis
import fit
import run_detail
import storage
from garmin_client import DATASETS, GarminClient, GarminError, resolve_api_key

ROOT = Path(__file__).resolve().parent
ON_VERCEL = bool(os.environ.get("VERCEL"))
CACHE = Path(tempfile.gettempdir()) / "health-insight" if ON_VERCEL else ROOT / ".cache"
CACHE_TTL_S = 15 * 60
MIN_FETCH_DAYS = 120  # baselines, 12-week running history and fitness estimates need this much


def _demo() -> bool:
    return os.environ.get("DEMO_MODE") == "1"


def zone(tz: str | None) -> ZoneInfo:
    for name in (tz, os.environ.get("APP_TIMEZONE"), "UTC"):
        if name:
            try:
                return ZoneInfo(name)
            except Exception:  # noqa: BLE001
                continue
    return ZoneInfo("UTC")


def today_in(tz: str | None) -> date:
    return datetime.now(zone(tz)).date()


# ------------------------------------------------------------------ auth

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
    time.sleep(0.5)
    return False, None


def _gate(authorization):
    ok, misconfig = check_auth(authorization)
    if misconfig:
        return 500, {"error": misconfig}
    if not ok:
        return 401, {"error": "Password required", "auth": True}
    return None


# ------------------------------------------------------------------ fetching

_client: GarminClient | None = None


def shared_client() -> GarminClient:
    global _client
    if _client is None:
        _client = GarminClient(resolve_api_key(), CACHE)
    return _client


def fetch_raw(fetch_days: int, end: date, demo: bool) -> dict:
    start = end - timedelta(days=fetch_days - 1)
    meta = {"fetchStart": start.isoformat(), "end": end.isoformat(), "fetchDays": fetch_days,
            "fetchedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"), "demo": demo}
    if demo:
        from demo_data import generate
        return {"meta": meta, "sets": {k: {"ok": True, "data": v} for k, v in generate(start, end).items()}}
    client = shared_client()
    client.token()  # fail fast on a bad key

    def run(name):
        try:
            return name, {"ok": True, "data": DATASETS[name](client, start, end)}
        except GarminError as e:
            return name, {"ok": False, "error": str(e)}
        except Exception as e:  # noqa: BLE001
            return name, {"ok": False, "error": f"{type(e).__name__}: {e}"}

    with ThreadPoolExecutor(max_workers=8) as pool:
        sets = dict(pool.map(run, DATASETS))
    return {"meta": meta, "sets": sets}


def _write_private(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump(obj, fh)


def get_raw(view_days: int, tz: str | None, refresh: bool) -> dict:
    demo = _demo() or _LOCAL_DEMO
    fetch_days = max(view_days, MIN_FETCH_DAYS)
    end = today_in(tz)
    f = CACHE / f"raw-{'demo' if demo else 'live'}-{fetch_days}-{end.isoformat()}.json"
    if not refresh and f.is_file() and time.time() - f.stat().st_mtime < CACHE_TTL_S:
        return json.loads(f.read_text())
    raw = fetch_raw(fetch_days, end, demo)
    _write_private(f, raw)
    return raw


_LOCAL_DEMO = False


def set_local_demo(v: bool) -> None:
    global _LOCAL_DEMO
    _LOCAL_DEMO = v


def _sources(raw: dict) -> dict:
    out = {}
    for k, s in raw["sets"].items():
        d = s.get("data")
        n = len(d) if isinstance(d, list) else (len(analysis.day_records(d)) or len(d)) if isinstance(d, dict) else 0
        out[k] = {"ok": s["ok"], "error": s.get("error"), "count": n}
    return out


# ------------------------------------------------------------------ per-run detail (FIT files)

RUN_DETAIL_DAYS = 120
_details_mem: dict | None = None
_details_loaded = 0.0


def load_details() -> dict:
    """Saved per-run results; re-read every 10 min so a warm instance sees the morning job's work."""
    global _details_mem, _details_loaded
    if _details_mem is None or time.time() - _details_loaded > 600:
        try:
            fresh = storage.get_json("run-details.json") or {}
        except Exception:  # noqa: BLE001
            fresh = {}
        _details_mem = {**(_details_mem or {}), **fresh}
        _details_loaded = time.time()
    return _details_mem


def _fetch_detail(act: dict, demo: bool) -> dict:
    try:
        if demo:
            from demo_data import demo_fit
            blob = demo_fit(act)
        else:
            blob = shared_client().get_bytes(f"/download-service/files/activity/{int(act['id'])}")
        det = run_detail.analyse_fit(fit.decode(fit.unzip_fit(blob)), act["id"])
        return det or {"v": run_detail.VERSION, "id": act["id"], "error": "no usable samples", "at": time.time()}
    except Exception as e:  # noqa: BLE001 - remember the failure so we don't retry on every visit
        return {"v": run_detail.VERSION, "id": act["id"], "error": f"{type(e).__name__}: {e}"[:300], "at": time.time()}


def ensure_details(acts: list[dict], end: date, budget_s: float, max_new: int, only_ids=None) -> tuple[dict, int]:
    """Download + analyse activity files for recent runs not seen yet. Returns (details, still_pending)."""
    demo = _demo() or _LOCAL_DEMO
    details = load_details()
    cutoff = (end - timedelta(days=RUN_DETAIL_DAYS)).isoformat()

    def needs(a):
        d = details.get(str(a["id"]))
        if d is None or d.get("v") != run_detail.VERSION:
            return True
        return bool(d.get("error")) and time.time() - d.get("at", 0) > 86400  # retry failures daily

    runs = [a for a in acts if a["family"] == "run" and a.get("id") is not None and a["date"] >= cutoff]
    if only_ids is not None:
        runs = [a for a in runs if a["id"] in only_ids]
    todo = [a for a in runs if needs(a)]
    batch = todo[:max_new]
    started = time.time()
    changed = False
    if batch:
        pool = ThreadPoolExecutor(max_workers=6)
        futs = [pool.submit(_fetch_detail, a, demo) for a in batch]
        for fu in futs:
            remaining = budget_s - (time.time() - started)
            try:
                det = fu.result(timeout=max(0.1, remaining))
            except Exception:  # noqa: BLE001 - out of time; picked up on the next visit
                continue
            details[str(det["id"])] = det
            changed = True
        pool.shutdown(wait=False, cancel_futures=True)
    if changed:
        keep = {str(a["id"]) for a in acts if a["family"] == "run"}
        for k in [k for k in details if k not in keep]:
            details.pop(k)  # forget runs that fell out of the window
        try:
            storage.put_json("run-details.json", details)
        except Exception:  # noqa: BLE001
            pass
    pending = sum(1 for a in runs if needs(a))
    return details, pending


# ------------------------------------------------------------------ briefings

def _remember_tz(tz: str | None) -> None:
    if not tz:
        return
    try:
        cur = storage.get_json("settings.json") or {}
        if cur.get("tz") != tz:
            storage.put_json("settings.json", {**cur, "tz": tz})
    except Exception:  # noqa: BLE001 - settings are best effort
        pass


def sync_briefing(an: dict, today: str, source: str, force: bool) -> tuple[dict | None, str | None]:
    """Store today's briefing if missing, forced, or if last night's data has arrived since."""
    try:
        stored = storage.get_json(f"briefings/{today}.json")
        fresh = analysis.briefing(an, today, source)
        if force or stored is None or (not stored.get("hasLastNight") and fresh["hasLastNight"]):
            storage.put_json(f"briefings/{today}.json", fresh)
            return fresh, None
        return stored, None
    except Exception as e:  # noqa: BLE001
        return None, f"{type(e).__name__}: {e}"


# ------------------------------------------------------------------ handlers

def _q(query, key, default=None):
    return (query.get(key) or [default])[0]


def handle_data(query: dict, authorization: str | None) -> tuple[int, dict]:
    if (g := _gate(authorization)):
        return g
    try:
        view = max(7, min(365, int(_q(query, "days", "90"))))
    except ValueError:
        view = 90
    tz, refresh = _q(query, "tz"), _q(query, "refresh", "0") == "1"
    try:
        raw = get_raw(view, tz, refresh)
    except GarminError as e:
        hint = " Check that your key is valid and not expired or revoked (Garmin Connect > Settings > Personal API)." if e.status in (401, 403) else ""
        return 502, {"error": str(e) + hint}
    if (name := _q(query, "raw")):
        s = raw["sets"].get(name)
        return (200, s) if s else (404, {"error": "unknown data set"})
    acts = analysis.build_activities(raw["sets"])
    details, pending = ensure_details(acts, date.fromisoformat(raw["meta"]["end"]), budget_s=20, max_new=15)
    an = analysis.analyse(raw, details)
    end = raw["meta"]["end"]
    _remember_tz(tz)
    brief, berr = sync_briefing(an, end, "refresh" if refresh else "visit", force=refresh)
    view_start = (date.fromisoformat(end) - timedelta(days=view - 1)).isoformat()
    meta = {**raw["meta"], "start": view_start, "days": view, "storage": storage.backend(), "storageError": berr,
            "runDetails": {"analysed": sum(1 for d in details.values() if not d.get("error")), "failed": sum(1 for d in details.values() if d.get("error")), "pending": pending}}
    return 200, {"meta": meta, "analysis": an, "briefing": brief, "sources": _sources(raw)}


def _intraday(raw_day: dict, tz: ZoneInfo) -> dict:
    """Pull plottable intraday series out of per-day endpoints (shapes vary, so search by name)."""
    out = {}

    def walk(o, key):
        if isinstance(o, dict):
            for k, v in o.items():
                if k == key:
                    return v
                r = walk(v, key)
                if r is not None:
                    return r
        elif isinstance(o, list):
            for x in o:
                r = walk(x, key)
                if r is not None:
                    return r
        return None

    def local_min(ms):
        t = datetime.fromtimestamp(ms / 1000, tz=timezone.utc).astimezone(tz)
        return t.hour * 60 + t.minute + t.second / 60

    hr = walk(raw_day.get("heartRate"), "heartRateValues")
    if isinstance(hr, list):
        pts = [(p[0], p[1]) for p in hr if isinstance(p, list) and len(p) >= 2 and analysis._num(p[0]) and analysis._num(p[1])]
        step = max(1, len(pts) // 288)
        out["hr"] = [[round(local_min(t), 1), v] for t, v in pts[::step]]
    levels = walk(raw_day.get("sleep"), "sleepLevels")
    if isinstance(levels, list):
        segs = []
        for s in levels:
            try:
                a = datetime.fromisoformat(str(s["startGMT"]).replace("Z", "")).replace(tzinfo=timezone.utc).astimezone(tz)
                b = datetime.fromisoformat(str(s["endGMT"]).replace("Z", "")).replace(tzinfo=timezone.utc).astimezone(tz)
                segs.append({"start": a.isoformat(timespec="minutes"), "end": b.isoformat(timespec="minutes"), "stage": int(s.get("activityLevel", 1))})
            except (KeyError, ValueError, TypeError):
                continue
        out["sleepLevels"] = segs
    readings = walk(raw_day.get("hrv"), "hrvReadings")
    if isinstance(readings, list):
        out["hrvReadings"] = [{"time": str(r.get("readingTimeLocal", ""))[11:16], "value": r.get("hrvValue")} for r in readings if isinstance(r, dict) and analysis._num(r.get("hrvValue"))]
    return out


def handle_day(query: dict, authorization: str | None) -> tuple[int, dict]:
    if (g := _gate(authorization)):
        return g
    tz = _q(query, "tz")
    target = _q(query, "date") or today_in(tz).isoformat()
    try:
        tday = date.fromisoformat(target)
    except ValueError:
        return 400, {"error": "date must be YYYY-MM-DD"}
    today = today_in(tz)
    if tday > today:
        return 400, {"error": "That date is in the future."}
    age = (today - tday).days
    view = MIN_FETCH_DAYS if age < MIN_FETCH_DAYS - 35 else 365
    if age > 365 - 35:
        return 400, {"error": "Daily view covers the last ~11 months."}
    try:
        raw = get_raw(view, tz, False)
    except GarminError as e:
        return 502, {"error": str(e)}
    acts = analysis.build_activities(raw["sets"])
    day_ids = {a["id"] for a in acts if a["date"] == target}
    details, _ = ensure_details(acts, today, budget_s=25, max_new=6, only_ids=day_ids)
    an = analysis.analyse(raw, details)
    rep = analysis.day_report(an["days"], an["activities"], an["load"], target)
    if rep is None:
        return 404, {"error": "No data for that date."}
    # intraday detail (cached per date; today's refreshes with the normal TTL)
    zi = zone(tz)
    f = CACHE / f"day-{'demo' if (_demo() or _LOCAL_DEMO) else 'live'}-{target}.json"
    if f.is_file() and (age >= 2 or time.time() - f.stat().st_mtime < CACHE_TTL_S):
        raw_day = json.loads(f.read_text())
    else:
        if _demo() or _LOCAL_DEMO:
            from demo_data import generate_day
            raw_day = generate_day(tday, zi)
        else:
            c = shared_client()
            calls = {
                "sleep": lambda: c.get("/sleep-service/sleep/dailySleep", {"date": target}),
                "heartRate": lambda: c.get("/wellness-service/wellness/dailyHeartRate", {"date": target}),
                "hrv": lambda: c.get(f"/hrv-service/hrv/{target}"),
            }
            raw_day = {}
            with ThreadPoolExecutor(max_workers=3) as pool:
                futs = {k: pool.submit(fn) for k, fn in calls.items()}
                for k, fu in futs.items():
                    try:
                        raw_day[k] = fu.result()
                    except Exception:  # noqa: BLE001
                        raw_day[k] = None
        _write_private(f, raw_day)
    rep["intraday"] = _intraday(raw_day, zi)
    bb = None
    for d, rec in analysis.day_records((raw["sets"].get("bodyBattery") or {}).get("data") or []):
        if d == target and isinstance(rec.get("bodyBatteryValuesArray"), list):
            pts = []
            for p in rec["bodyBatteryValuesArray"]:
                nums = [x for x in (p if isinstance(p, list) else []) if analysis._num(x)]
                ts = next((x for x in nums if x > 1e11), None)
                lvl = next((x for x in reversed(nums) if 0 <= x <= 100), None)
                if ts and lvl is not None:
                    t = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).astimezone(zi)
                    if t.date() == tday:
                        pts.append([round(t.hour * 60 + t.minute, 1), lvl])
            bb = sorted(pts) or None
            break
    if bb:
        rep["intraday"]["bodyBattery"] = bb
    try:
        rep["briefing"] = storage.get_json(f"briefings/{target}.json")
    except Exception:  # noqa: BLE001
        rep["briefing"] = None
    rep["hrProfile"] = an["hrProfile"]
    rep["runs"] = [analysis.describe_run(a, details.get(str(a["id"])), an["hrProfile"], (an.get("running") or {}).get("fitness")) for a in rep["activities"] if a["family"] == "run"]
    rep["range"] = {"min": (today - timedelta(days=330)).isoformat(), "max": today.isoformat()}
    return 200, rep


def handle_cron(authorization: str | None) -> tuple[int, dict]:
    secret = os.environ.get("CRON_SECRET", "")
    if not secret or not hmac.compare_digest((authorization or "").encode(), f"Bearer {secret}".encode()):
        return 401, {"error": "unauthorized"}
    try:
        tz = (storage.get_json("settings.json") or {}).get("tz")
    except Exception:  # noqa: BLE001
        tz = None
    try:
        raw = get_raw(MIN_FETCH_DAYS, tz, True)
    except GarminError as e:
        return 502, {"error": str(e)}
    acts = analysis.build_activities(raw["sets"])
    details, pending = ensure_details(acts, date.fromisoformat(raw["meta"]["end"]), budget_s=35, max_new=40)
    an = analysis.analyse(raw, details)
    brief, err = sync_briefing(an, raw["meta"]["end"], "scheduled", force=True)
    if err:
        return 500, {"error": err}
    return 200, {"ok": True, "date": brief["date"], "tz": tz, "readiness": (brief.get("readiness") or {}).get("score"), "runDetailsPending": pending}
