"""Tests for the analysis engine (insights, running maths, daily report)."""

import sys
import unittest
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import analysis  # noqa: E402
import demo_data  # noqa: E402


def payload(end=date(2026, 11, 17), days=120, sets=None):
    start = end - timedelta(days=days - 1)
    sets = sets or {k: {"ok": True, "data": v} for k, v in demo_data.generate(start, end).items()}
    return {"meta": {"fetchStart": start.isoformat(), "end": end.isoformat()}, "sets": sets}


def runner_sets(end, km_per_week=50):
    """A consistent runner: 5 runs/week at ~5:15/km with a weekly 5 km race-effort run."""
    start = end - timedelta(days=119)
    acts, daily = [], []
    for i in range(120):
        d = start + timedelta(days=i)
        daily.append({"calendarDate": d.isoformat(), "restingHeartRate": 50, "lastNightAvg": 60, "sleepTimeSeconds": 27000})
        wd = d.weekday()
        if wd in (0, 4):
            continue
        km = {6: km_per_week * 0.3}.get(wd, km_per_week * 0.7 / 4)
        hard = wd == 2
        dur = 1200 if hard else km * 315  # 5 km in 20:00 on Wednesdays
        acts.append({"activityId": i, "activityName": "Run", "activityType": {"typeKey": "running"},
                     "startTimeLocal": f"{d.isoformat()} 07:00:00", "duration": dur, "distance": 5000 if hard else km * 1000,
                     "averageHR": 172 if hard else 140, "maxHR": 185 if hard else 150, "aerobicTrainingEffect": 4.0 if hard else 2.8,
                     "averageRunningCadenceInStepsPerMinute": 172})
    acts.sort(key=lambda a: a["startTimeLocal"], reverse=True)
    return {"activities": {"ok": True, "data": acts}, "dailySummaries": {"ok": True, "data": {"data": daily}},
            "hrZones": {"ok": True, "data": [{"sport": "DEFAULT", "zone1Floor": 100, "zone2Floor": 120, "zone3Floor": 142, "zone4Floor": 160, "zone5Floor": 175}]}}


class RunningMaths(unittest.TestCase):
    def test_vdot_matches_daniels_table(self):
        # Daniels' tables: 5K in 20:00 ~ VDOT 49.8; that VDOT predicts ~41:2x for 10K
        v = analysis.vdot_from(5000, 20)
        self.assertAlmostEqual(v, 49.8, delta=0.3)
        t10 = analysis.predict_time(v, 10000)
        self.assertTrue(41.0 <= t10 <= 41.8, t10)

    def test_runner_report(self):
        end = date(2026, 11, 17)
        an = analysis.analyse(payload(end, sets=runner_sets(end)))
        r = an["running"]
        self.assertAlmostEqual(r["fitness"]["vdot"], 49.8, delta=0.5)
        self.assertEqual([p["race"] for p in r["predictions"]], ["5K", "10K", "Half marathon", "Marathon"])
        self.assertTrue(40 <= r["nextWeek"]["kmLow"] <= r["nextWeek"]["kmHigh"] <= 60, r["nextWeek"])
        # plan adds up to roughly the suggested distance and keeps the long run longest
        self.assertLessEqual(abs(r["planTotalKm"] - r["nextWeek"]["kmHigh"]), 4)
        longs = [p for p in r["plan"] if p["type"] == "long"]
        self.assertEqual(len(longs), 1)
        self.assertIn("quality", {p["type"] for p in r["plan"]})
        self.assertEqual(len(r["plan"]), 7)
        self.assertIn(r["today"]["type"], {"rest", "easy", "long", "threshold", "intervals", "done"})


class Insights(unittest.TestCase):
    def test_demo_produces_all_groups(self):
        an = analysis.analyse(payload())
        for group in ("health", "wellbeing", "training"):
            self.assertTrue(an["insights"][group], group)
            for x in an["insights"][group]:
                self.assertIn(x["level"], {"good", "info", "warning", "serious"})
                self.assertTrue(x["text"])
        self.assertIsNotNone(an["readiness"])
        self.assertTrue(0 <= an["readiness"]["score"] <= 100)

    def test_strain_flag_when_markers_move_together(self):
        end = date(2026, 11, 17)
        start = end - timedelta(days=29)
        recs = []
        for i in range(30):
            d = start + timedelta(days=i)
            bad = i >= 28
            recs.append({"calendarDate": d.isoformat(), "restingHeartRate": 58 if bad else 50,
                         "lastNightAvg": 40 if bad else 60, "averageRespirationValue": 16.5 if bad else 14.5})
        sets = {"dailySummaries": {"ok": True, "data": recs}, "hrv": {"ok": True, "data": recs}, "sleep": {"ok": True, "data": recs}}
        an = analysis.analyse(payload(end, 30, sets))
        self.assertTrue(an["flags"]["illness"])
        self.assertEqual(an["insights"]["health"][0]["title"], "Early strain warning")
        self.assertLessEqual(an["readiness"]["score"], 45)

    def test_missing_data_is_tolerated(self):
        empty = {k: {"ok": False, "error": "x"} for k in ("sleep", "hrv", "activities", "steps")}
        an = analysis.analyse(payload(sets=empty))
        self.assertIsNone(an["readiness"])
        self.assertIsNone(an["running"])

    def test_day_report_and_briefing(self):
        an = analysis.analyse(payload())
        rep = analysis.day_report(an["days"], an["activities"], an["load"], "2026-11-16")
        self.assertEqual(rep["date"], "2026-11-16")
        self.assertTrue(rep["notes"])
        b = analysis.briefing(an, "2026-11-17", "scheduled")
        self.assertEqual(b["source"], "scheduled")
        self.assertIn("insights", b)


if __name__ == "__main__":
    unittest.main()
