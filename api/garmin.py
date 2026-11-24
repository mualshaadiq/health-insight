"""GET /api/garmin - put the base plan on the Garmin Connect calendar (?status=1 to check, ?dry=1 to preview)."""
import sys
from http.server import BaseHTTPRequestHandler
from pathlib import Path

sys.path[:0] = [str(Path(__file__).resolve().parent), str(Path(__file__).resolve().parent.parent)]

import core  # noqa: E402
from _handler import respond  # noqa: E402


class handler(BaseHTTPRequestHandler):  # noqa: N801 - name required by Vercel
    def do_GET(self):  # noqa: N802
        respond(self, core.handle_garmin, with_query=True)
