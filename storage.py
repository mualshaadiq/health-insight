"""Tiny JSON store: private Vercel Blob when BLOB_READ_WRITE_TOKEN is set, else local files.

Uses the same HTTP protocol as Vercel's official SDK (vercel-blob), without the dependency.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

API = os.environ.get("VERCEL_BLOB_API_URL", "https://vercel.com/api/blob").rstrip("/")
PREFIX = "health-insight/"


def _token() -> str | None:
    return os.environ.get("BLOB_READ_WRITE_TOKEN") or None


def _store_id(token: str) -> str:
    rest = token.removeprefix("vercel_blob_rw_")
    return rest.split("_", 1)[0].removeprefix("store_").lower()


def _local_dir() -> Path:
    import tempfile
    base = Path(tempfile.gettempdir()) / "health-insight" if os.environ.get("VERCEL") else Path(__file__).resolve().parent / ".cache"
    return base / "store"


def backend() -> str:
    return "vercel-blob" if _token() else "local"


def put_json(path: str, obj) -> None:
    body = json.dumps(obj, separators=(",", ":")).encode()
    tok = _token()
    if not tok:
        f = _local_dir() / path
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(body)
        return
    req = urllib.request.Request(
        f"{API}/?{urllib.parse.urlencode({'pathname': PREFIX + path})}",
        data=body, method="PUT",
        headers={
            "authorization": f"Bearer {tok}", "x-api-version": "12",
            "x-vercel-blob-access": "private", "x-add-random-suffix": "0",
            "x-allow-overwrite": "1", "x-content-type": "application/json",
            "x-cache-control-max-age": "60",
        },
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        r.read()


def get_json(path: str):
    tok = _token()
    if not tok:
        f = _local_dir() / path
        return json.loads(f.read_text()) if f.is_file() else None
    url = f"https://{_store_id(tok)}.private.blob.vercel-storage.com/{urllib.parse.quote(PREFIX + path)}"
    req = urllib.request.Request(url, headers={"authorization": f"Bearer {tok}", "cache-control": "no-cache"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise
