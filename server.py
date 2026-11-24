#!/usr/bin/env python3
"""Health Insight - local dashboard server for your Garmin data.

    python3 server.py                 # serve http://127.0.0.1:8787 with live Garmin data
    python3 server.py --demo          # synthetic data, no key needed
    python3 server.py --export out.json --days 90   # write a data snapshot and exit

The same app also deploys to Vercel (see api/data.py and vercel.json).
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import sys
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from garmin_client import GarminError, load_dotenv

ROOT = Path(__file__).resolve().parent
WEB = ROOT / "web"


class Handler(BaseHTTPRequestHandler):
    server_version = "HealthInsight/1.0"

    def log_message(self, fmt, *args):
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
        import core

        url = urlsplit(self.path)
        if url.path == "/api/workouts":
            status, body, ctype, fname = core.handle_workouts(parse_qs(url.query), self.headers.get("Authorization"))
            data = body if isinstance(body, bytes) else json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            if fname:
                self.send_header("Content-Disposition", f'attachment; filename="{fname}"')
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        routes = {"/api/data": core.handle_data, "/api/day": core.handle_day}
        if url.path in routes:
            return self._json(*routes[url.path](parse_qs(url.query), self.headers.get("Authorization")))
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


def main() -> None:
    load_dotenv(ROOT / ".env")
    import core  # after .env is loaded

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8787)))
    ap.add_argument("--host", default="127.0.0.1", help="bind address (default: localhost only)")
    ap.add_argument("--demo", action="store_true", help="serve synthetic data; no key required")
    ap.add_argument("--export", metavar="FILE", help="write a JSON snapshot and exit")
    ap.add_argument("--days", type=int, default=90, help="days of history for --export")
    args = ap.parse_args()

    if not args.demo:
        try:
            core.shared_client()
        except GarminError as e:
            sys.exit(f"error: {e}\n(or run with --demo to preview the dashboard)")

    core.set_local_demo(args.demo)
    if args.export:
        data = core.fetch_raw(max(args.days, core.MIN_FETCH_DAYS), core.today_in(None), args.demo)
        Path(args.export).write_text(json.dumps(data, indent=1))
        bad = [k for k, v in data["sets"].items() if not v["ok"]]
        print(f"wrote {args.export}" + (f" (failed: {', '.join(bad)})" if bad else ""))
        return

    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Health Insight running at http://{args.host}:{args.port}  ({'DEMO data' if args.demo else 'live Garmin data'})")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
