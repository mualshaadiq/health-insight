"""Shared plumbing for the Vercel Python functions in this folder."""

import json
from urllib.parse import parse_qs, urlsplit


def respond(h, fn, with_query=True):
    """Run a core handler for a BaseHTTPRequestHandler instance and write the JSON response."""
    auth = h.headers.get("Authorization")
    status, payload = fn(parse_qs(urlsplit(h.path).query), auth) if with_query else fn(auth)
    body = json.dumps(payload).encode()
    h.send_response(status)
    h.send_header("Content-Type", "application/json")
    h.send_header("Cache-Control", "private, no-store")
    h.send_header("X-Robots-Tag", "noindex")
    h.send_header("Content-Length", str(len(body)))
    h.end_headers()
    h.wfile.write(body)
