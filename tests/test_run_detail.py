"""FIT decoding and per-run analysis (best efforts, intervals, hill adjustment, caching)."""

import io
import struct
import sys
import tempfile
import unittest
import zipfile
from datetime import date
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import fit  # noqa: E402
import run_detail  # noqa: E402

T0 = 1_700_000_000


def make_run(paces, hr=140, climb_per_km=0.0, laps=None):
    """paces: list of seconds-per-km for each km. One record per second."""
    recs, dist, t, alt = [], 0.0, 0, 100.0
    for p in paces:
        v = 1000 / p
        for _ in range(int(round(p))):
            dist += v
            alt += climb_per_km * v / 1000
            recs.append({"t": T0 + t, "dist": dist, "speed": v, "hr": hr, "cad": 85, "alt": alt})
            t += 1
    laps = laps or [{"start": T0, "end": T0 + t, "sec": t, "dist": dist, "hr": hr, "maxhr": hr, "intensity": 0}]
    return fit.encode(recs, laps, {"start": T0, "end": T0 + t, "sec": t, "dist": dist})


class FitDecoding(unittest.TestCase):
    def test_roundtrip_and_scaling(self):
        out = fit.decode(make_run([300, 300], hr=150, climb_per_km=10))
        self.assertEqual(len(out["records"]), 600)
        r = out["records"][-1]
        self.assertAlmostEqual(r["distance"], 2000, delta=1)
        self.assertAlmostEqual(r["enhanced_speed"], 1000 / 300, places=2)
        self.assertEqual(r["heart_rate"], 150)
        self.assertAlmostEqual(r["enhanced_altitude"], 120, delta=0.5)
        self.assertEqual(r["timestamp"], T0 + 599)
        self.assertEqual(len(out["laps"]), 1)
        self.assertEqual(out["sessions"][0]["sport"], 1)

    def test_zip_wrapper(self):
        raw = make_run([300] * 2)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("123_ACTIVITY.fit", raw)
        self.assertEqual(fit.unzip_fit(buf.getvalue()), raw)
        self.assertEqual(fit.unzip_fit(raw), raw)

    def test_compressed_timestamps_and_big_endian(self):
        body = bytearray()
        # local 0, big-endian definition: record {timestamp u32, heart_rate u8}
        body += bytes([0x40, 0, 1]) + struct.pack(">H", 20) + bytes([2, 253, 4, 0x86, 3, 1, 0x02])
        body += bytes([0x00]) + struct.pack(">I", 1000) + bytes([100])
        # local 1, little-endian definition: record {heart_rate}
        body += bytes([0x41, 0, 0]) + struct.pack("<H", 20) + bytes([1, 3, 1, 0x02])
        body += bytes([0x80 | (1 << 5) | 5, 101])  # compressed header, offset 5 (< 1000 & 31 = 8, so rolls over)
        header = struct.pack("<BBHI4s", 12, 0x20, 2132, len(body), b".FIT")
        out = fit.decode(header + bytes(body) + b"\0\0")
        self.assertEqual([r["heart_rate"] for r in out["records"]], [100, 101])
        self.assertEqual(out["records"][1]["timestamp"], 1029 + fit.FIT_EPOCH)

    def test_rejects_non_fit(self):
        with self.assertRaises(fit.FitError):
            fit.decode(b"hello world, definitely not a FIT file")


class RunAnalysis(unittest.TestCase):
    def test_best_efforts_and_pacing(self):
        d = run_detail.analyse_fit(fit.decode(make_run([300] + [240] * 6)), 1)
        self.assertAlmostEqual(d["best"]["5k"]["sec"], 1200, delta=2)
        self.assertAlmostEqual(d["best"]["1k"]["sec"], 240, delta=2)
        self.assertNotIn("10k", d["best"])
        self.assertEqual(len(d["splits"]), 7)
        self.assertAlmostEqual(d["splits"][0]["sec"], 300, delta=1)
        self.assertLess(d["halves"]["pct"], 0)  # second half faster
        self.assertEqual(d["gapSec"], d["paceSec"])  # flat: hill-adjusted == actual
        self.assertIsNone(d["intervals"])

    def test_hills_make_adjusted_pace_faster(self):
        d = run_detail.analyse_fit(fit.decode(make_run([330] * 6, climb_per_km=60)), 1)
        self.assertGreater(d["elevGain"], 300)
        self.assertLess(d["gapSec"], d["paceSec"])

    def test_intervals_from_workout_laps(self):
        paces, laps, t, dist = [], [], 0, 0.0
        for kind, sec, pace in [("w", 600, 360)] + [("r", 180, 250), ("o", 120, 480)] * 5 + [("c", 300, 360)]:
            n = int(sec)
            v = 1000 / pace
            laps.append({"start": T0 + t, "end": T0 + t + n, "sec": n, "dist": v * n, "hr": 165 if kind == "r" else 125,
                         "maxhr": 170, "intensity": {"w": 2, "r": 0, "o": 1, "c": 3}[kind]})
            paces += [pace] * 0
            t += n
            dist += v * n
        # build records matching the laps
        recs, d_, tt = [], 0.0, 0
        for lap in laps:
            v = lap["dist"] / lap["sec"]
            for _ in range(lap["sec"]):
                d_ += v
                recs.append({"t": T0 + tt, "dist": d_, "speed": v, "hr": lap["hr"], "cad": 85, "alt": 50})
                tt += 1
        blob = fit.encode(recs, laps, {"start": T0, "end": T0 + tt, "sec": tt, "dist": d_})
        d = run_detail.analyse_fit(fit.decode(blob), 7)
        iv = d["intervals"]
        self.assertEqual(iv["reps"], 5)
        self.assertEqual(iv["repSec"], 180)
        self.assertAlmostEqual(iv["paceSec"], 250, delta=1)
        self.assertLess(iv["cvPct"], 1)
        self.assertEqual(iv["hrWork"], 165)
        self.assertEqual(iv["hrRest"], 125)
        self.assertFalse(d["steady"])

    def test_decoupling_on_steady_run(self):
        # 50 min at a constant pace with HR creeping up -> positive drift
        recs, d_ = [], 0.0
        v = 1000 / 330
        for t in range(3000):
            d_ += v
            recs.append({"t": T0 + t, "dist": d_, "speed": v, "hr": 130 + t // 300, "cad": 85, "alt": 50})
        blob = fit.encode(recs, [{"start": T0, "end": T0 + 3000, "sec": 3000, "dist": d_, "hr": 135, "maxhr": 140, "intensity": 0}],
                          {"start": T0, "end": T0 + 3000, "sec": 3000, "dist": d_})
        d = run_detail.analyse_fit(fit.decode(blob), 9)
        self.assertTrue(d["steady"])
        self.assertGreater(d["decoupling"], 2)
        self.assertAlmostEqual(run_detail.time_above(d, 135), 0.6, delta=0.15)


class Caching(unittest.TestCase):
    def test_details_fetched_once_and_failures_not_retried(self):
        import core
        import storage
        tmp = tempfile.TemporaryDirectory()
        acts = [{"id": i, "family": "run", "date": "2026-11-1%d" % i, "dur": 1800, "dist": 6000, "name": "Run", "hour": 7} for i in range(1, 4)]
        calls = []

        def fake_fetch(a, demo):
            calls.append(a["id"])
            if a["id"] == 3:
                return {"v": run_detail.VERSION, "id": 3, "error": "boom", "at": 9e18}
            return {"v": run_detail.VERSION, "id": a["id"], "distM": 6000}

        with mock.patch.object(storage, "_local_dir", lambda: Path(tmp.name)), mock.patch.object(core, "_fetch_detail", fake_fetch), \
                mock.patch.object(core, "_details_mem", None), mock.patch.object(storage, "_token", lambda: None):
            det, pending = core.ensure_details(acts, date(2026, 11, 17), 10, 10)
            self.assertEqual(sorted(calls), [1, 2, 3])
            self.assertEqual(pending, 0)
            core.ensure_details(acts, date(2026, 11, 17), 10, 10)
            self.assertEqual(len(calls), 3)  # nothing refetched, failure remembered
            self.assertTrue(storage.get_json("run-details.json"))
        tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
