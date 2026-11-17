"""Offline tests: run a fake Garmin server and check the key -> token -> data flow.

    python3 -m unittest discover -s tests
"""

import base64
import json
import sys
import tempfile
import threading
import time
import unittest
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from garmin_client import DATASETS, TOKEN_PATH, GarminClient, GarminError  # noqa: E402


def jwt(exp):
    enc = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")
    return f"{enc({'alg': 'none'})}.{enc({'exp': exp})}.sig"


class Fake(BaseHTTPRequestHandler):
    calls: list = []
    token = jwt(time.time() + 86400)
    reject_first_bearer = False

    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json"):
        b = body.encode() if isinstance(body, str) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_POST(self):
        Fake.calls.append(("POST", self.path, self.headers.get("Authorization")))
        if self.path == TOKEN_PATH and self.headers.get("Authorization") == "ApiKey good-key":
            return self._send(200, Fake.token + "\n", "text/plain")
        self._send(401, {"message": "bad key"})

    def do_GET(self):
        Fake.calls.append(("GET", self.path, self.headers.get("Authorization")))
        if self.headers.get("Authorization") != f"Bearer {Fake.token}":
            return self._send(401, {"message": "bad token"})
        if Fake.reject_first_bearer:
            Fake.reject_first_bearer = False
            return self._send(401, {"message": "revoked"})
        if self.path.startswith("/activitylist-service"):
            return self._send(200, [{"activityId": 1}] if "start=0" in self.path else [])
        self._send(200, [{"calendarDate": "2026-11-01", "path": self.path}])


class ClientTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), Fake)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.srv.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        Fake.calls.clear()
        self.tmp = tempfile.TemporaryDirectory()
        self.cache = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def client(self, key="good-key"):
        return GarminClient(key, self.cache, base_url=self.base)

    def test_token_exchange_and_cache(self):
        c = self.client()
        self.assertEqual(c.token(), Fake.token)
        self.assertEqual(Fake.calls[0], ("POST", TOKEN_PATH, "ApiKey good-key"))
        self.assertEqual(oct((self.cache / "bearer-token.json").stat().st_mode & 0o777), "0o600")
        # a fresh client reuses the cached token without another POST
        self.client().token()
        self.assertEqual(sum(1 for m, *_ in Fake.calls if m == "POST"), 1)

    def test_bad_key(self):
        with self.assertRaises(GarminError) as ctx:
            self.client("nope").token()
        self.assertEqual(ctx.exception.status, 401)

    def test_401_refreshes_token_once(self):
        Fake.reject_first_bearer = True
        self.assertEqual(self.client().get("/x")[0]["calendarDate"], "2026-11-01")

    def test_ranged_endpoints_are_chunked_and_merged(self):
        out = DATASETS["steps"](self.client(), date(2026, 1, 1), date(2026, 3, 1))  # 60 days -> 3 chunks
        self.assertEqual(len(out), 3)
        self.assertIn("/usersummary-service/stats/steps/daily/2026-01-01/2026-01-28", out[0]["path"])

    def test_all_datasets_hit_the_api(self):
        c = self.client()
        for name, fn in DATASETS.items():
            self.assertIsNotNone(fn(c, date(2026, 11, 1), date(2026, 11, 7)), name)


if __name__ == "__main__":
    unittest.main()
