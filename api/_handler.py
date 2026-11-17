"""Shared plumbing for the Vercel Python functions in this folder."""

import json
import sys
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def make_handler(fn, with_query=True):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            auth = self.headers.get("Authorization")
            if with_query:
                status, payload = fn(parse_qs(urlsplit(self.path).query), auth)
            else:
                status, payload = fn(auth)
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "private, no-store")
            self.send_header("X-Robots-Tag", "noindex")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return Handler
