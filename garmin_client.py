"""Minimal, dependency-free client for the Garmin Connect Personal API.

Flow (per Garmin's docs): POST your Personal API key to the token endpoint with
``Authorization: ApiKey <key>``; the response body is a JWT bearer token valid
for up to 24 h. Data endpoints are then called with ``Authorization: Bearer``.
Endpoint paths mirror Garmin's official client:
https://github.com/garmin/connect-personal-api-python
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, timedelta
from pathlib import Path

DEFAULT_BASE_URL = "https://personal.connectapi.garmin.com"
TOKEN_PATH = "/personalapitoken-service/personal-api/personal-api-key/token"
DEFAULT_KEY_FILE = Path("~/.config/garmin/connect-personal-api-key").expanduser()
REFRESH_WINDOW_S = 60 * 60  # renew a token when < 1 h of life remains
TIMEOUT_S = 30


class GarminError(Exception):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def load_dotenv(path: Path) -> None:
    """Load KEY=VALUE lines from a .env file without overriding the real env."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip("'\""))


def resolve_api_key() -> str:
    key = os.environ.get("GARMIN_PERSONAL_API_KEY", "").strip()
    if not key and DEFAULT_KEY_FILE.is_file():
        key = DEFAULT_KEY_FILE.read_text(encoding="utf-8").strip()
    if not key:
        raise GarminError(
            "No Garmin Personal API key found. Put GARMIN_PERSONAL_API_KEY=... in .env "
            f"or save the key to {DEFAULT_KEY_FILE}."
        )
    return key


def _jwt_exp(token: str) -> float:
    try:
        payload = token.split(".")[1]
        data = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        return float(data["exp"])
    except Exception as exc:  # noqa: BLE001
        raise GarminError("Token endpoint did not return a JWT with an exp claim") from exc


class GarminClient:
    def __init__(self, api_key: str, cache_dir: Path, base_url: str | None = None):
        self.api_key = api_key
        self.base_url = (base_url or os.environ.get("GARMIN_PERSONAL_API_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        self.cache_dir = cache_dir
        self.token_file = cache_dir / "bearer-token.json"
        self._token: str | None = None
        self._exp = 0.0

    # ---------- auth ----------
    def _fingerprint(self) -> str:
        return hashlib.sha256(f"{self.base_url}\0{self.api_key}".encode()).hexdigest()

    def token(self, force: bool = False) -> str:
        now = time.time()
        if not force and self._token and self._exp - now > REFRESH_WINDOW_S:
            return self._token
        if not force and self.token_file.is_file():
            try:
                c = json.loads(self.token_file.read_text())
                if c["fingerprint"] == self._fingerprint() and float(c["expires_at"]) - now > REFRESH_WINDOW_S:
                    self._token, self._exp = c["access_token"], float(c["expires_at"])
                    return self._token
            except (ValueError, KeyError):
                pass
        req = urllib.request.Request(
            self.base_url + TOKEN_PATH,
            method="POST",
            headers={"Authorization": f"ApiKey {self.api_key}", "Accept": "*/*"},
        )
        body = self._send(req).decode().strip()
        self._token, self._exp = body, _jwt_exp(body)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.token_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump({"fingerprint": self._fingerprint(), "access_token": body, "expires_at": self._exp}, f)
        return self._token

    # ---------- http ----------
    @staticmethod
    def _send(req: urllib.request.Request) -> bytes:
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:300]
            raise GarminError(f"HTTP {e.code} for {req.get_method()} {urllib.parse.urlsplit(req.full_url).path}: {detail}", e.code) from None
        except urllib.error.URLError as e:
            raise GarminError(f"Network error reaching Garmin: {e.reason}") from None

    def get_bytes(self, path: str, accept: str = "application/zip, application/octet-stream") -> bytes:
        """GET a binary resource (e.g. the original activity file as a ZIP)."""
        for attempt in (0, 1):
            req = urllib.request.Request(
                self.base_url + path,
                headers={"Authorization": f"Bearer {self.token(force=attempt == 1)}", "Accept": accept},
            )
            try:
                return self._send(req)
            except GarminError as e:
                if e.status == 401 and attempt == 0:
                    continue
                raise
        return b""

    def get(self, path: str, params: dict | None = None):
        qs = ("?" + urllib.parse.urlencode(params, doseq=True)) if params else ""
        for attempt in (0, 1):
            req = urllib.request.Request(
                self.base_url + path + qs,
                headers={"Authorization": f"Bearer {self.token(force=attempt == 1)}", "Accept": "application/json"},
            )
            try:
                raw = self._send(req)
                return json.loads(raw) if raw.strip() else None
            except GarminError as e:
                if e.status == 401 and attempt == 0:
                    continue  # token revoked/expired early: fetch a fresh one once
                raise
        return None


# ---------- data sets used by the dashboard ----------

def _chunks(start: date, end: date, size: int):
    cur = start
    while cur <= end:
        stop = min(cur + timedelta(days=size - 1), end)
        yield cur, stop
        cur = stop + timedelta(days=1)


def _merge(parts: list):
    """Concatenate chunked responses: lists concat, {'data': [...]}-style dicts concat their lists."""
    parts = [p for p in parts if p is not None]
    if not parts:
        return None
    if all(isinstance(p, list) for p in parts):
        return [x for p in parts for x in p]
    if all(isinstance(p, dict) for p in parts):
        out = dict(parts[0])
        for k, v in out.items():
            if isinstance(v, list):
                out[k] = [x for p in parts for x in (p.get(k) or [])]
        return out
    return parts


def ranged(client: GarminClient, start: date, end: date, fn, chunk_days: int = 28):
    return _merge([fn(a.isoformat(), b.isoformat()) for a, b in _chunks(start, end, chunk_days)])


DATASETS = {
    # name: callable(client, start, end) -> json
    "profile": lambda c, s, e: c.get("/userprofile-service/userprofile/settings"),
    "dailySummaries": lambda c, s, e: ranged(c, s, e, lambda a, b: c.get("/usersummary-service/v2/dailysummaries", {"startDate": a, "endDate": b})),
    "steps": lambda c, s, e: ranged(c, s, e, lambda a, b: c.get(f"/usersummary-service/stats/steps/daily/{a}/{b}")),
    "stress": lambda c, s, e: ranged(c, s, e, lambda a, b: c.get(f"/usersummary-service/stats/stress/daily/{a}/{b}")),
    "sleep": lambda c, s, e: ranged(c, s, e, lambda a, b: c.get("/sleep-service/sleep/dailySleepSummariesByDate", {"startDate": a, "endDate": b})),
    "hrv": lambda c, s, e: ranged(c, s, e, lambda a, b: c.get(f"/hrv-service/hrv/daily/{a}/{b}")),
    "bodyBattery": lambda c, s, e: ranged(c, s, e, lambda a, b: c.get("/wellness-service/wellness/bodyBattery/reports/daily", {"startDate": a, "endDate": b})),
    "vo2max": lambda c, s, e: ranged(c, s, e, lambda a, b: c.get(f"/metrics-service/metrics/maxmet/daily/{a}/{b}")),
    "weight": lambda c, s, e: c.get(f"/weight-service/weight/range/{s.isoformat()}/{e.isoformat()}", {"includeAll": "true"}),
    "hrZones": lambda c, s, e: c.get("/biometric-service/heartRateZones/v2"),
    "fitnessAge": lambda c, s, e: c.get(f"/fitnessage-service/fitnessage/v2/{e.isoformat()}"),
    "activities": lambda c, s, e: _all_activities(c, s, e),
}


def _all_activities(c: GarminClient, s: date, e: date, page: int = 100, cap: int = 1000):
    out: list = []
    while len(out) < cap:
        batch = c.get(
            "/activitylist-service/activities/search/activities",
            {"start": len(out), "limit": page, "startDate": s.isoformat(), "endDate": e.isoformat()},
        )
        if not isinstance(batch, list) or not batch:
            break
        out.extend(batch)
        if len(batch) < page:
            break
    return out
