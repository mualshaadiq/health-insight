"""Synthetic Garmin-shaped data for `server.py --demo`.

Field names follow Garmin Connect's JSON so the dashboard's parser is exercised
the same way as with live data. Nothing here is real.
"""

from __future__ import annotations

import math
import random
from datetime import date, datetime, timedelta, timezone

SPORTS = [  # typeKey, name, weight, (min, max) minutes, speed m/s, hr
    ("running", "Run", 4, (25, 75), 2.9, 152),
    ("cycling", "Ride", 2, (45, 120), 7.5, 138),
    ("strength_training", "Strength", 3, (35, 60), 0, 118),
    ("lap_swimming", "Swim", 1, (30, 50), 0.9, 135),
    ("walking", "Walk", 2, (30, 70), 1.4, 102),
    ("yoga", "Yoga", 1, (30, 50), 0, 92),
]


def _local_ms(d: date, hour: int, minute: int) -> int:
    """Garmin 'Local' timestamps: wall-clock time encoded as if it were UTC."""
    t = datetime(d.year, d.month, d.day, tzinfo=timezone.utc) + timedelta(hours=hour, minutes=minute)
    return int(t.timestamp() * 1000)


def demo_fit(act: dict) -> bytes:
    """A synthetic FIT file for a demo run: easy, long, tempo or intervals, with hills and HR drift."""
    import fit
    rnd = random.Random(int(act["id"]))
    start = datetime.fromisoformat(f"{act['date']}T{(act.get('hour') or 7):02d}:00:00").replace(tzinfo=timezone.utc).timestamp()
    total = act["dur"]
    base_v = (act["dist"] or total * 2.8) / total
    kind = rnd.choices(["easy", "intervals", "tempo"], [6, 2, 2])[0] if total < 4200 else "long"
    hilly = rnd.random() < 0.4
    recs, laps = [], []
    dist, t = 0.0, 0
    lap_start, lap_dist, lap_hr = 0, 0.0, []
    segs = []  # (seconds, speed factor, intensity)
    if kind == "intervals":
        segs.append((600, 0.85, 2))
        for _ in range(rnd.randint(4, 6)):
            segs += [(rnd.choice([180, 240]), 1.25, 0), (120, 0.6, 1)]
        segs.append((max(300, total - sum(x[0] for x in segs) - 300), 0.85, 3))
    elif kind == "tempo":
        segs = [(600, 0.85, 2), (1200, 1.12, 0), (max(300, total - 1800), 0.85, 3)]
    else:
        segs = [(total, 1.0, 0)]
    hr = 100.0
    drift = 0.06 if kind == "long" else 0.02
    for sec, factor, intensity in segs:
        lap_start, lap_dist, lap_hr = t, 0.0, []
        for _ in range(int(sec)):
            frac = t / total
            grade = 0.04 * math.sin(dist / 400) if hilly else 0.0
            v = base_v * factor * (1 - grade * 2.5) * (1 + rnd.uniform(-0.03, 0.03))
            target = 95 + 92 * (factor - 0.6) + 120 * grade + 100 * drift * frac
            hr += (target - hr) * 0.05
            dist += v
            lap_dist += v
            lap_hr.append(hr)
            recs.append({"t": start + t, "dist": dist, "speed": v, "hr": round(hr), "cad": round(82 + 6 * factor), "alt": 50 + (30 * (1 - math.cos(dist / 400)) if hilly else 0),
                         "temp": 31 if (act.get("hour") or 7) >= 12 else 28})
            t += 1
        laps.append({"start": start + lap_start, "end": start + t, "sec": t - lap_start, "dist": lap_dist, "hr": round(sum(lap_hr) / len(lap_hr)), "maxhr": round(max(lap_hr)), "intensity": intensity})
    if kind in ("easy", "long"):  # auto 1 km laps instead of one big lap
        laps = []
        k, ls, ld = 1, 0, 0.0
        for i, r in enumerate(recs):
            if r["dist"] >= k * 1000 or i == len(recs) - 1:
                laps.append({"start": start + ls, "end": start + i + 1, "sec": i + 1 - ls, "dist": r["dist"] - ld, "hr": r["hr"], "maxhr": r["hr"], "intensity": 0})
                k, ls, ld = k + 1, i + 1, r["dist"]
    return fit.encode(recs, laps, {"start": start, "end": start + t, "sec": t, "dist": dist})


def generate_day(d: date, tz) -> dict:
    """Synthetic per-day detail: intraday heart rate, sleep stages and overnight HRV readings."""
    rnd = random.Random(d.toordinal())
    start_local = datetime(d.year, d.month, d.day, tzinfo=tz) - timedelta(hours=1, minutes=30)
    t, segs, cycle = start_local, [], [(1, 20), (0, 35), (1, 25), (2, 15), (1, 20), (0, 25), (2, 25), (3, 3), (1, 30), (2, 30), (1, 25), (0, 10), (2, 35), (3, 5), (1, 20)]
    for stage, mins in cycle:
        e = t + timedelta(minutes=mins + rnd.randint(-5, 5))
        segs.append({"startGMT": t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.0"), "endGMT": e.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.0"), "activityLevel": stage})
        t = e
    day0 = datetime(d.year, d.month, d.day, tzinfo=tz)
    hr = []
    for i in range(0, 24 * 60, 2):
        h = i / 60
        base = 50 if h < 6.5 else 68 + 8 * math.sin(h / 3)
        if 17.5 <= h <= 18.5:
            base = 145 + rnd.uniform(-8, 10)
        hr.append([int((day0 + timedelta(minutes=i)).timestamp() * 1000), round(base + rnd.uniform(-3, 3))])
    readings = [{"hrvValue": round(55 + rnd.uniform(-12, 12)), "readingTimeLocal": (start_local + timedelta(minutes=5 * k)).strftime("%Y-%m-%dT%H:%M:%S.0")} for k in range(0, 96)]
    return {"sleep": {"dailySleepDTO": {"calendarDate": d.isoformat()}, "sleepLevels": segs},
            "heartRate": {"calendarDate": d.isoformat(), "heartRateValues": hr},
            "hrv": {"hrvSummary": {"calendarDate": d.isoformat()}, "hrvReadings": readings}}


def generate(start: date, end: date, seed: int = 7) -> dict:
    rnd = random.Random(seed)
    days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    daily, steps, stress, sleep, hrv, bb, vo2, weight, acts = [], [], [], [], [], [], [], [], []
    fatigue = 0.0
    act_id = 19_000_000_000
    w = 74.2
    for i, d in enumerate(days):
        ds = d.isoformat()
        wk = d.weekday()
        # training (rest on Mon + random)
        sessions = []
        if wk != 0 and rnd.random() < 0.72:
            pool = [s for s in SPORTS for _ in range(s[2])]
            sessions.append(rnd.choice(pool))
            if wk in (5, 6) and rnd.random() < 0.25:
                sessions.append(rnd.choice(pool))
        load_today = 0.0
        for typ, name, _, (lo, hi), spd, hr in sessions:
            mins = rnd.uniform(lo, hi) * (1.25 if wk in (5, 6) else 1.0)
            avg_hr = hr + rnd.randint(-8, 8)
            z = [rnd.random() for _ in range(5)]
            shape = {"running": [1, 3, 4, 2, .6], "cycling": [1, 4, 3, 1, .3], "strength_training": [3, 4, 2, .5, .1]}.get(typ, [4, 3, 1, .2, 0])
            z = [a * b for a, b in zip(z, shape)]
            zt = sum(z) or 1
            zones = [round(mins * 60 * x / zt) for x in z]
            load = sum((k + 1) * zones[k] / 60 for k in range(5)) * 0.9
            load_today += load
            act_id += rnd.randint(1000, 9000)
            hh = rnd.choice([6, 7, 12, 17, 18]) if wk < 5 else rnd.choice([7, 8, 9, 16])
            acts.append({
                "activityId": act_id,
                "activityName": f"{name}" if rnd.random() < .6 else f"Morning {name}" if hh < 11 else f"Evening {name}",
                "activityType": {"typeKey": typ},
                "startTimeLocal": f"{ds} {hh:02d}:{rnd.randint(0, 59):02d}:00",
                "duration": round(mins * 60, 1),
                "distance": round(mins * 60 * spd * rnd.uniform(.9, 1.1), 1) if spd else None,
                "averageHR": avg_hr, "maxHR": avg_hr + rnd.randint(12, 30),
                "calories": round(mins * (avg_hr - 50) * 0.12),
                "aerobicTrainingEffect": round(min(5, 1.2 + load / 45 + rnd.uniform(-.3, .3)), 1),
                "anaerobicTrainingEffect": round(rnd.uniform(0, 2.5), 1),
                "activityTrainingLoad": round(load, 1),
                "elevationGain": round(rnd.uniform(5, 220)) if typ in ("running", "cycling", "walking") else None,
                "averageRunningCadenceInStepsPerMinute": round(rnd.uniform(158, 172)) if typ == "running" else None,
                **{f"hrTimeInZone_{k + 1}": zones[k] for k in range(5)},
            })
        fatigue = fatigue * 0.8 + load_today / 60
        # recovery
        trend = math.sin(i / 11) * 2
        rhr = round(52 + fatigue * 0.7 - trend * .5 + rnd.uniform(-1.5, 1.5))
        hrv_night = round(max(30, 62 - fatigue * 1.8 + trend * 2 + rnd.uniform(-6, 6)))
        sl = max(4.6, min(9.2, rnd.gauss(7.2 if wk in (4, 5) else 6.9, .7)))
        deep, rem, awake = sl * rnd.uniform(.14, .22), sl * rnd.uniform(.18, .26), rnd.uniform(.1, .6)
        light = sl - deep - rem
        score = round(max(35, min(96, 48 + (sl - 5.5) * 12 + (deep / sl - .16) * 120 - awake * 10 + rnd.uniform(-5, 5))))
        bb_high = round(max(25, min(100, 40 + score * .55 - fatigue * 4 + rnd.uniform(-6, 6))))
        bb_low = round(max(5, bb_high - rnd.uniform(35, 70)))
        st = round(max(12, min(70, 30 + fatigue * 3 - (sl - 7) * 4 + rnd.uniform(-8, 8))))
        stp = round(max(1500, rnd.gauss(9500 if wk < 5 else 11000, 2600)))
        is_today = d == end
        if is_today:  # today is still in progress
            stp = round(stp * .45)
        sleep.append({
            "calendarDate": ds,
            "sleepTimeSeconds": round(sl * 3600),
            "deepSleepSeconds": round(deep * 3600), "lightSleepSeconds": round(light * 3600),
            "remSleepSeconds": round(rem * 3600), "awakeSleepSeconds": round(awake * 3600),
            "sleepScores": {"overall": {"value": score}},
            "avgOvernightHrv": hrv_night,
            "sleepStartTimestampLocal": _local_ms(d - timedelta(days=1), 22 + (1 if wk in (5, 6) else 0), rnd.randint(0, 59) + (40 if wk in (5, 6) else 0)),
            "sleepEndTimestampLocal": _local_ms(d, 6, rnd.randint(0, 59)),
            "averageRespirationValue": round(rnd.uniform(13, 16), 1),
        })
        hrv.append({"calendarDate": ds, "lastNightAvg": hrv_night, "weeklyAvg": None,
                    "baseline": {"balancedLow": 49, "balancedUpper": 66}})
        steps.append({"calendarDate": ds, "totalSteps": stp, "stepGoal": 10000, "totalDistance": round(stp * .78)})
        stress.append({"calendarDate": ds, "values": {"overallStressLevel": st}})
        base_ms = int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp() * 1000)
        def level(h, hi=bb_high, lo=bb_low):  # charge overnight, drain through the day
            h = h % 24
            if h < 6.5:
                return lo + 10 + (hi - lo - 10) * h / 6.5
            return hi - (hi - lo) * ((h - 6.5) / 17.5) ** 0.8
        # span ±12 h so the curve covers the local day in any timezone
        bb.append({"date": ds, "charged": round(bb_high - 15), "drained": round(bb_high - bb_low),
                   "bodyBatteryValuesArray": [[base_ms + int(h * 3600000), round(level(h))] for h in [x / 2 for x in range(-24, 72)]]})
        daily.append({"calendarDate": ds, "heartRate": {"restingHeartRate": rhr},
                      "calories": {"activeKilocalories": round(stp * .04 + load_today * 4)},
                      "intensityMinutes": {"moderateIntensityMinutes": rnd.randint(5, 40),
                                           "vigorousIntensityMinutes": round(load_today / 6)}})
        if sessions and sessions[0][0] in ("running", "cycling"):
            vo2.append({"generic": {"calendarDate": ds, "vo2MaxPreciseValue": round(51.5 + i * .012 + rnd.uniform(-.3, .3), 1)}})
        if i % 3 == 0:
            w += rnd.uniform(-.35, .3)
            weight.append({"summaryDate": ds, "weight": round(w * 1000)})
    for k, s in enumerate(hrv):
        win = [h["lastNightAvg"] for h in hrv[max(0, k - 6):k + 1]]
        s["weeklyAvg"] = round(sum(win) / len(win))
    acts.sort(key=lambda a: a["startTimeLocal"], reverse=True)
    return {
        "profile": {"userData": {"gender": "MALE", "weight": round(w * 1000), "height": 178}},
        "dailySummaries": {"data": daily},
        "steps": steps, "stress": stress, "sleep": sleep,
        "hrv": {"hrvSummaries": hrv}, "bodyBattery": bb, "vo2max": vo2,
        "weight": {"dateWeightList": weight},
        "hrZones": [{"sport": "DEFAULT", "zone1Floor": 98, "zone2Floor": 117, "zone3Floor": 137, "zone4Floor": 156, "zone5Floor": 176}],
        "fitnessAge": {"chronologicalAge": 34, "fitnessAge": 29.4},
        "activities": acts,
    }


if __name__ == "__main__":  # quick sanity check
    out = generate(date.today() - timedelta(days=29), date.today())
    print({k: (len(v) if isinstance(v, list) else type(v).__name__) for k, v in out.items()})
    print(datetime.now())
