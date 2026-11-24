"""Garmin Connect calendar sync: workout building (garminconnect models) and idempotent scheduling."""

import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    import garminconnect  # noqa: F401
    HAVE_LIB = True
except ImportError:
    HAVE_LIB = False

import garmin_sync  # noqa: E402

BASE = {"cap": 150, "walkAbove": 155, "resumeBelow": 140, "jog": 2, "walk": 1, "easyMin": "35–40", "longMin": 50}


class FakeGarmin:
    def __init__(self):
        self.lib, self.cal, self.next_id = {}, [], 100
        self.deleted, self.unscheduled = [], []

    def get_workouts(self, start, limit):
        return [{"workoutId": i, "workoutName": n} for i, n in self.lib.items()]

    def get_scheduled_workouts_by_period(self, start, end):
        return [c for c in self.cal if start <= c["date"] <= end]

    def upload_running_workout(self, w):
        self.next_id += 1
        self.lib[self.next_id] = w.workoutName
        return {"workoutId": self.next_id}

    def schedule_workout(self, wid, d):
        self.next_id += 1
        self.cal.append({"workoutScheduleId": self.next_id, "date": d, "workoutName": self.lib[wid], "workoutId": wid})

    def unschedule_workout(self, sid):
        self.unscheduled.append(sid)
        self.cal = [c for c in self.cal if c["workoutScheduleId"] != sid]

    def delete_workout(self, wid):
        self.deleted.append(wid)
        self.lib.pop(wid)

    def send_scheduled_workouts_to_devices(self):
        return None


@unittest.skipUnless(HAVE_LIB, "garminconnect not installed")
class Build(unittest.TestCase):
    def test_easy_workout_payload(self):
        w = garmin_sync.build_workout("easy", BASE, False).to_dict()
        self.assertEqual(w["sportType"]["sportTypeKey"], "running")
        steps = w["workoutSegments"][0]["workoutSteps"]
        self.assertEqual([s["stepType"]["stepTypeKey"] for s in steps], ["warmup", "warmup", "repeat", "cooldown"])
        rep = steps[2]
        self.assertEqual(rep["numberOfIterations"], 8)
        jog, walk = rep["workoutSteps"]
        self.assertEqual(jog["targetType"]["workoutTargetTypeKey"], "heart.rate.zone")
        self.assertEqual((jog["targetValueOne"], jog["targetValueTwo"]), (90.0, 150.0))
        self.assertEqual(jog["endConditionValue"], 120)
        self.assertEqual(walk["stepType"]["stepTypeKey"], "recovery")
        self.assertEqual(steps[1]["targetValueTwo"], 145.0)  # warm-up jog capped below the cap
        orders = [steps[0]["stepOrder"], steps[1]["stepOrder"], rep["stepOrder"], jog["stepOrder"], walk["stepOrder"], steps[3]["stepOrder"]]
        self.assertEqual(orders, sorted(set(orders)))
        self.assertEqual(w["estimatedDurationInSecs"], 600 + 8 * 180 + 300)

    def test_strides_and_continuous(self):
        w = garmin_sync.build_workout("easy", BASE, True).to_dict()
        keys = [s["stepType"]["stepTypeKey"] for s in w["workoutSegments"][0]["workoutSteps"]]
        self.assertEqual(keys, ["warmup", "warmup", "repeat", "repeat", "cooldown"])
        c = garmin_sync.build_workout("long", {**BASE, "jog": None, "walk": None, "longMin": 80}, False).to_dict()
        main = c["workoutSegments"][0]["workoutSteps"][2]
        self.assertEqual(main["endConditionValue"], 65 * 60)


@unittest.skipUnless(HAVE_LIB, "garminconnect not installed")
class Sync(unittest.TestCase):
    def test_idempotent_and_follows_ladder(self):
        g = FakeGarmin()
        monday = date(2026, 11, 23)
        r1 = garmin_sync.sync(g, BASE, monday)
        self.assertEqual([s.split()[0] for s in r1["scheduled"]], ["2026-11-24", "2026-11-26", "2026-11-28"])
        self.assertEqual(len(g.lib), 3)
        r2 = garmin_sync.sync(g, BASE, monday)
        self.assertEqual(r2["scheduled"], [])
        self.assertEqual(len(r2["kept"]), 3)
        # move up the ladder: old items unscheduled, old templates deleted, new ones scheduled
        r3 = garmin_sync.sync(g, {**BASE, "jog": 3, "easyMin": "40–45", "longMin": 60}, monday)
        self.assertEqual(len(r3["removed"]), 3)
        self.assertEqual(len(r3["scheduled"]), 3)
        self.assertEqual(len(g.deleted), 3)
        self.assertTrue(all("3:1" in n for n in g.lib.values()))

    def test_leaves_other_workouts_alone_and_skips_done_today(self):
        g = FakeGarmin()
        g.lib[1] = "My own tempo"
        g.cal.append({"workoutScheduleId": 9, "date": "2026-11-24", "workoutName": "My own tempo", "workoutId": 1})
        r = garmin_sync.sync(g, BASE, date(2026, 11, 24), ran_today=True)
        self.assertNotIn("2026-11-24", " ".join(r["scheduled"]))
        self.assertIn(1, g.lib)
        self.assertEqual(g.unscheduled, [])

    def test_dry_run_changes_nothing(self):
        g = FakeGarmin()
        r = garmin_sync.sync(g, BASE, date(2026, 11, 23), dry_run=True)
        self.assertEqual(len(r["scheduled"]), 3)
        self.assertEqual(g.lib, {})


if __name__ == "__main__":
    unittest.main()
