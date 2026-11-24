"""Workout files for the watch: structure, checksums and plan sizing."""

import io
import struct
import sys
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import fit  # noqa: E402
import workouts  # noqa: E402

BASE = {"cap": 150, "walkAbove": 155, "resumeBelow": 140, "jog": 2, "walk": 1, "easyMin": "35–40", "longMin": 50}


class WorkoutFiles(unittest.TestCase):
    def test_valid_fit_with_checksums(self):
        for name, data in workouts.plan_files(BASE).items():
            self.assertEqual(data[8:12], b".FIT", name)
            size = struct.unpack_from("<I", data, 4)[0]
            self.assertEqual(len(data), 14 + size + 2)
            self.assertEqual(struct.unpack_from("<H", data, 12)[0], fit._crc16(data[:12]))
            self.assertEqual(struct.unpack_from("<H", data, len(data) - 2)[0], fit._crc16(data[:-2]))

    def test_session_structure(self):
        steps = workouts._session("x", 40, BASE)
        self.assertEqual([s["kind"] for s in steps], ["time", "time", "time", "time", "repeat", "time"])
        rep = steps[4]
        self.assertEqual(rep["from_step"], 2)
        total = 10 + rep["times"] * 3 + 5
        self.assertTrue(37 <= total <= 43, total)
        self.assertEqual(steps[2]["hr"], (90, 150))
        long_ = workouts._session("x", 50, BASE)
        self.assertEqual(long_[4]["times"], 12)
        cont = workouts._session("x", 45, {**BASE, "jog": None, "walk": None})
        self.assertEqual(cont[2]["sec"], 30 * 60)
        strides = workouts._session("x", 40, BASE, strides=True)
        self.assertEqual(strides[7], {"kind": "repeat", "from_step": 5, "times": 6})

    def test_zip(self):
        z = zipfile.ZipFile(io.BytesIO(workouts.plan_zip(BASE)))
        names = z.namelist()
        self.assertEqual(sum(n.endswith(".fit") for n in names), 3)
        self.assertIn("HOW_TO_INSTALL.txt", names)


if __name__ == "__main__":
    unittest.main()
