"""GET /api/workouts - zip of Garmin workout files (.fit) for the current base-plan step."""

import json
import sys
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

sys.path[:0] = [str(Path(__file__).resolve().parent), str(Path(__file__).resolve().parent.parent)]

import core  # noqa: E402


class handler(BaseHTTPRequestHandler):  # noqa: N801 - name required by Vercel
    def do_GET(self):  # noqa: N802
        status, body, ctype, fname = core.handle_workouts(parse_qs(urlsplit(self.path).query), self.headers.get("Authorization"))
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "private, no-store")
        if fname:
            self.send_header("Content-Disposition", f'attachment; filename="{fname}"')
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)
