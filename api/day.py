"""GET /api/day?date=YYYY-MM-DD - one day's detail, intraday series and that morning's briefing."""

import sys
from http.server import BaseHTTPRequestHandler
from pathlib import Path

sys.path[:0] = [str(Path(__file__).resolve().parent), str(Path(__file__).resolve().parent.parent)]

import core  # noqa: E402
from _handler import respond  # noqa: E402


class handler(BaseHTTPRequestHandler):  # noqa: N801 - name required by Vercel
    def do_GET(self):  # noqa: N802
        respond(self, core.handle_day, with_query=True)
