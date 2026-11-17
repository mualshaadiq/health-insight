#!/usr/bin/env python3
"""Health Insight - local dashboard server for your Garmin data.

    python3 server.py                 # serve http://127.0.0.1:8787 with live Garmin data
    python3 server.py --demo          # synthetic data, no key needed
    python3 server.py --export out.json --days 90   # write a data snapshot and exit

Your key never leaves this machine except in the request to Garmin's token
endpoint; the browser only ever talks to this local server.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from garmin_client import DATASETS, GarminClient, GarminError, load_dotenv, resolve_api_key

ROOT = Path(__file__).resolve().parent
WEB = ROOT / "web"
CACHE = ROOT / ".cache"
CACHE_TTL_S = 15 * 60


def fetch_all(client: GarminClient | None, days: int, demo: bool) -> dict:
    end = date.today()
    start = end - timedelta(days=days - 1)
    meta = {"start": start.isoformat(), "end": end.isoformat(), "days": days,
            "fetchedAt": datetime.now().isoformat(timespec="seconds"), "demo": demo}
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

    with ThreadPoolExecutor(max_workers=4) as pool:
        sets = dict(pool.map(run, DATASETS))
    return {"meta": meta, "sets": sets}


class Handler(BaseHTTPRequestHandler):
    server_version = "HealthInsight/1.0"
    client: GarminClient | None = None
    demo = False

    def log_message(self, fmt, *args):  # quieter logs, never print query strings with secrets (there are none)
        sys.stderr.write("[%s] %s\n" % (time.strftime("%H:%M:%S"), fmt % args))

    def _json(self, status: int, payload) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        url = urlsplit(self.path)
        if url.path == "/api/data":
            return self._data(parse_qs(url.query))
        rel = "index.html" if url.path in ("", "/") else url.path.lstrip("/")
        f = (WEB / rel).resolve()
        if WEB not in f.parents or not f.is_file():
            return self.send_error(HTTPStatus.NOT_FOUND)
        body = f.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(f.name)[0] or "application/octet-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _data(self, q):
        try:
            days = max(7, min(365, int(q.get("days", ["90"])[0])))
        except ValueError:
            days = 90
        refresh = q.get("refresh", ["0"])[0] == "1"
        cache_file = CACHE / f"data-{'demo' if self.demo else 'live'}-{days}.json"
        if not refresh and cache_file.is_file() and time.time() - cache_file.stat().st_mtime < CACHE_TTL_S:
            return self._json(200, json.loads(cache_file.read_text()))
        try:
            payload = fetch_all(self.client, days, self.demo)
        except GarminError as e:
            hint = ""
            if e.status in (401, 403):
                hint = " Check that your key is valid and not expired/revoked at connect.garmin.com > Settings > Personal API."
            return self._json(502, {"error": str(e) + hint})
        CACHE.mkdir(exist_ok=True)
        fd = os.open(cache_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            json.dump(payload, fh)
        return self._json(200, payload)


def main() -> None:
    load_dotenv(ROOT / ".env")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8787)))
    ap.add_argument("--host", default="127.0.0.1", help="bind address (default: localhost only)")
    ap.add_argument("--demo", action="store_true", help="serve synthetic data; no key required")
    ap.add_argument("--export", metavar="FILE", help="write a JSON snapshot and exit")
    ap.add_argument("--days", type=int, default=90, help="days of history for --export")
    args = ap.parse_args()

    client = None
    if not args.demo:
        try:
            client = GarminClient(resolve_api_key(), CACHE)
        except GarminError as e:
            sys.exit(f"error: {e}\n(or run with --demo to preview the dashboard)")

    if args.export:
        data = fetch_all(client, args.days, args.demo)
        Path(args.export).write_text(json.dumps(data, indent=1))
        bad = [k for k, v in data["sets"].items() if not v["ok"]]
        print(f"wrote {args.export}" + (f" (failed: {', '.join(bad)})" if bad else ""))
        return

    Handler.client, Handler.demo = client, args.demo
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Health Insight running at http://{args.host}:{args.port}  ({'DEMO data' if args.demo else 'live Garmin data'})")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
