"""Turns raw Garmin JSON into normalised daily metrics, activities, insights and running advice.

Everything here is a heuristic for personal use, not medical advice. Field
lookup is tolerant because Garmin's response shapes differ between endpoints.
"""

from __future__ import annotations

import math
import statistics
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone

import run_detail

# ----------------------------------------------------------------- helpers

def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def mean(xs):
    xs = [x for x in xs if _num(x)]
    return sum(xs) / len(xs) if xs else None


def sd(xs):
    xs = [x for x in xs if _num(x)]
    return statistics.stdev(xs) if len(xs) > 1 else None


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def signed(v, d=0, unit=""):
    if not _num(v):
        return "–"
    s = "+" if v > 0 else "−" if v < 0 else "±"
    return f"{s}{abs(v):.{d}f}{unit}"


def hm(sec):
    if not _num(sec):
        return "–"
    m = round(sec / 60)
    return f"{m // 60}h {m % 60:02d}m"


def pace_str(sec_per_km):
    if not _num(sec_per_km) or sec_per_km <= 0:
        return "–"
    s = round(sec_per_km)
    return f"{s // 60}:{s % 60:02d}/km"


def dur_str(minutes):
    s = round(minutes * 60)
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _dig(o, path):
    for p in path.split("."):
        if not isinstance(o, dict):
            return None
        o = o.get(p)
    return o


def find(rec, keys):
    """First numeric value for any candidate key: dotted paths first, then breadth-first by name."""
    for k in keys:
        if "." in k:
            v = _dig(rec, k)
            if _num(v):
                return v
    want = {k.lower() for k in keys if "." not in k}
    queue = [rec]
    while queue:
        o = queue.pop(0)
        for k, v in o.items():
            if k.lower() in want and _num(v):
                return v
            if isinstance(v, dict):
                queue.append(v)
    return None


def find_str(rec, keys):
    want = {k.lower() for k in keys}
    queue = [rec]
    while queue:
        o = queue.pop(0)
        for k, v in o.items():
            if k.lower() in want and isinstance(v, str) and v:
                return v
            if isinstance(v, dict):
                queue.append(v)
    return None


DATE_KEYS = ("calendarDate", "summaryDate", "date", "calendarDay", "day", "startDate")


def _date_of(o):
    for k in DATE_KEYS:
        v = o.get(k)
        if isinstance(v, str) and len(v) >= 10 and v[4] == "-" and v[7] == "-":
            return v[:10]
        if _num(v) and v > 1e11:
            return datetime.fromtimestamp(v / 1000, tz=timezone.utc).date().isoformat()
    return None


def day_records(js, out=None):
    out = [] if out is None else out
    if isinstance(js, list):
        for x in js:
            day_records(x, out)
    elif isinstance(js, dict):
        d = _date_of(js)
        if d:
            out.append((d, js))
        else:
            for v in js.values():
                if isinstance(v, (dict, list)):
                    day_records(v, out)
    return out


# ----------------------------------------------------------------- normalise

METRICS = {
    "sleepSec": (["sleep"], ["sleepTimeSeconds", "totalSleepSeconds", "sleepTimeInSeconds", "sleepDurationInSeconds"]),
    "deepSec": (["sleep"], ["deepSleepSeconds", "deepSleepDurationInSeconds"]),
    "lightSec": (["sleep"], ["lightSleepSeconds", "lightSleepDurationInSeconds"]),
    "remSec": (["sleep"], ["remSleepSeconds", "remSleepDurationInSeconds"]),
    "awakeSec": (["sleep"], ["awakeSleepSeconds", "awakeDurationInSeconds"]),
    "sleepScore": (["sleep"], ["sleepScores.overall.value", "overallSleepScore.value", "sleepScore", "overallScore", "sleepScoreValue"]),
    "sleepStart": (["sleep"], ["sleepStartTimestampLocal"]),
    "sleepEnd": (["sleep"], ["sleepEndTimestampLocal"]),
    "respiration": (["sleep", "dailySummaries"], ["averageRespirationValue", "avgSleepRespirationValue", "avgWakingRespirationValue"]),
    "spo2": (["sleep", "dailySummaries"], ["averageSpO2Value", "avgSleepSpO2", "averageSpo2"]),
    "hrv": (["hrv", "sleep"], ["lastNightAvg", "lastNightAverage", "avgOvernightHrv", "hrvValue"]),
    "hrvLow": (["hrv"], ["baseline.balancedLow", "balancedLow"]),
    "hrvHigh": (["hrv"], ["baseline.balancedUpper", "balancedUpper"]),
    "rhr": (["dailySummaries", "sleep"], ["heartRate.restingValue", "restingHeartRate", "restingHeartRateValue", "restingHr", "currentDayRestingHeartRate"]),
    "steps": (["steps", "dailySummaries"], ["totalSteps", "steps"]),
    "stepGoal": (["steps", "dailySummaries"], ["stepGoal", "dailyStepGoal", "totalStepGoal"]),
    "stress": (["stress", "dailySummaries"], ["overallStressLevel", "averageStressLevel", "avgStressLevel"]),
    "bbHigh": (["bodyBattery", "dailySummaries"], ["bodyBatteryHighestValue", "highest", "bodyBatteryHigh", "maxBodyBattery"]),
    "bbLow": (["bodyBattery", "dailySummaries"], ["bodyBatteryLowestValue", "lowest", "bodyBatteryLow", "minBodyBattery"]),
    "bbCharged": (["bodyBattery", "dailySummaries"], ["charged", "bodyBatteryChargedValue"]),
    "bbDrained": (["bodyBattery", "dailySummaries"], ["drained", "bodyBatteryDrainedValue"]),
    "modMin": (["dailySummaries"], ["moderateIntensityMinutes", "intensityMinutes.moderate"]),
    "vigMin": (["dailySummaries"], ["vigorousIntensityMinutes", "intensityMinutes.vigorous"]),
    "activeKcal": (["dailySummaries"], ["activeKilocalories", "activeCalories", "calories.active"]),
    "vo2": (["vo2max"], ["vo2MaxPreciseValue", "vo2MaxValue", "vo2Max"]),
    "weight": (["weight"], ["weight", "latestWeight.weight"]),
}


def _bb_values(rec):
    arr = rec.get("bodyBatteryValuesArray")
    if not isinstance(arr, list):
        return []
    vals = []
    for p in arr:
        v = p[-1] if isinstance(p, list) and p else (p.get("value") if isinstance(p, dict) else None)
        if _num(v):
            vals.append(v)
    return vals


def build_days(sets: dict, start: date, end: date) -> list[dict]:
    dates = [(start + timedelta(days=i)).isoformat() for i in range((end - start).days + 1)]
    by = {d: {"date": d} for d in dates}
    status_done = set()
    for metric, (set_names, keys) in METRICS.items():
        for name in set_names:
            s = sets.get(name) or {}
            if not s.get("ok") or s.get("data") is None:
                continue
            for d, rec in day_records(s["data"]):
                day = by.get(d)
                if day is None or _num(day.get(metric)):
                    continue
                v = find(rec, keys)
                if v is None and metric in ("bbHigh", "bbLow"):
                    vals = _bb_values(rec)
                    if vals:
                        v = max(vals) if metric == "bbHigh" else min(vals)
                if metric == "weight" and _num(v) and v > 1000:
                    v = v / 1000
                if _num(v):
                    day[metric] = v
                if name == "hrv" and d not in status_done:
                    st = find_str(rec, ["status", "hrvStatus"])
                    if st:
                        day["hrvStatus"] = st.lower()
                        status_done.add(d)
    for day in by.values():
        for k in ("sleepSec", "hrv", "rhr", "sleepScore", "steps"):
            if day.get(k) == 0:
                day.pop(k)
        # "local" timestamps are epoch millis shifted to local wall-clock time
        for k in ("sleepStart", "sleepEnd"):
            if _num(day.get(k)):
                t = datetime.fromtimestamp(day[k] / 1000, tz=timezone.utc)
                day[k + "Min"] = t.hour * 60 + t.minute
            day.pop(k, None)
    return [by[d] for d in dates]


FAMILIES = [
    ("run", "Running", ("run", "track", "treadmill")),
    ("ride", "Cycling", ("cycl", "bik", "ride", "spin")),
    ("strength", "Strength", ("strength", "weight", "hiit", "crossfit", "boulder", "climb")),
    ("walk", "Walk & hike", ("walk", "hik")),
    ("swim", "Swimming", ("swim",)),
    ("other", "Other", ("",)),
]


def family_of(type_key: str) -> str:
    t = (type_key or "").lower()
    for key, _, pats in FAMILIES:
        if any(p in t for p in pats):
            return key
    return "other"


def build_activities(sets: dict) -> list[dict]:
    s = sets.get("activities") or {}
    raw = s.get("data") if s.get("ok") else []
    if isinstance(raw, dict):
        raw = raw.get("activityList") or []
    out = []
    for a in raw if isinstance(raw, list) else []:
        if not isinstance(a, dict):
            continue
        at = a.get("activityType")
        typ = (at.get("typeKey") if isinstance(at, dict) else at) or (a.get("activityTypeDTO") or {}).get("typeKey") or "other"
        start = str(a.get("startTimeLocal") or a.get("startTimeGMT") or "")
        if len(start) < 10:
            continue
        dur = next((a[k] for k in ("duration", "movingDuration", "elapsedDuration") if _num(a.get(k))), 0.0)
        zones = [a.get(f"hrTimeInZone_{i}") for i in range(1, 6)]
        zones = [z if _num(z) else None for z in zones]
        load = a.get("activityTrainingLoad")
        if not _num(load) and any(_num(z) for z in zones):
            load = sum((i + 1) * z / 60 for i, z in enumerate(zones) if _num(z)) * 0.9
        te = a.get("aerobicTrainingEffect")
        if not _num(load):
            load = dur / 60 * (te * 0.6 if _num(te) else 1.5)
        dist = a.get("distance")
        hour = int(start[11:13]) if len(start) >= 13 and start[11:13].isdigit() else None
        out.append({
            "id": a.get("activityId"), "name": a.get("activityName") or typ.replace("_", " ").title(),
            "type": typ, "family": family_of(typ), "date": start[:10], "hour": hour,
            "dur": float(dur), "dist": float(dist) if _num(dist) else None,
            "avgHR": a.get("averageHR") if _num(a.get("averageHR")) else a.get("averageHeartRate"),
            "maxHR": a.get("maxHR") if _num(a.get("maxHR")) else None,
            "load": float(load), "te": te if _num(te) else None,
            "anTe": a.get("anaerobicTrainingEffect") if _num(a.get("anaerobicTrainingEffect")) else None,
            "zones": zones, "kcal": a.get("calories") if _num(a.get("calories")) else None,
            "elev": a.get("elevationGain") if _num(a.get("elevationGain")) else None,
            "tempMax": a.get("maxTemperature") if _num(a.get("maxTemperature")) else None,
            "tempMin": a.get("minTemperature") if _num(a.get("minTemperature")) else None,
            "cadence": next((a[k] for k in ("averageRunningCadenceInStepsPerMinute", "averageRunCadence", "avgRunCadence") if _num(a.get(k))), None),
            "vo2": a.get("vO2MaxValue") if _num(a.get("vO2MaxValue")) else a.get("vo2MaxValue") if _num(a.get("vo2MaxValue")) else None,
        })
    out.sort(key=lambda x: (x["date"], x["hour"] or 0), reverse=True)
    return out


def hr_profile(sets: dict, acts: list[dict], rhr: float | None = None) -> dict:
    """Max HR, zones, and the easy cap.

    The easy cap uses heart-rate reserve (Karvonen): resting + 64.5% of (max − resting),
    which lands near 150 bpm for a resting HR of ~52 and max of ~204. Without a resting HR
    it falls back to Garmin's zone 3 floor. EASY_HR_CAP in the environment overrides it.
    """
    import os
    floors, max_hr = {}, None
    s = sets.get("hrZones") or {}
    data = s.get("data") if s.get("ok") else None
    zone_sets = data if isinstance(data, list) else [data] if isinstance(data, dict) else []
    zone_sets = [z for z in zone_sets if isinstance(z, dict)]
    zone_sets.sort(key=lambda z: 0 if str(z.get("sport", "")).upper() in ("RUNNING", "DEFAULT") else 1)
    for z in zone_sets:
        for i in range(1, 6):
            v = find(z, [f"zone{i}Floor"])
            if _num(v):
                floors.setdefault(i, v)
        mh = find(z, ["maxHeartRateUsed", "maxHeartRate"])
        if _num(mh):
            max_hr = max_hr or mh
    seen = sorted(a["maxHR"] for a in acts if _num(a.get("maxHR")))
    if not max_hr and seen:
        max_hr = seen[int(len(seen) * 0.95) - 1 if len(seen) > 1 else 0]
    method = "Garmin zone 3"
    easy = floors.get(3) or (round(max_hr * 0.80) if max_hr else None)
    if max_hr and _num(rhr) and 35 <= rhr <= 90:
        easy = round(rhr + 0.645 * (max_hr - rhr))
        method = f"65% of heart-rate reserve (resting {rhr:.0f}, max {max_hr:.0f})"
    try:
        override = int(os.environ.get("EASY_HR_CAP", "0"))
    except ValueError:
        override = 0
    if 100 <= override <= 200:
        easy, method = override, "set manually"
    threshold = floors.get(4) or (round(max_hr * 0.88) if max_hr else None)
    return {"maxHR": max_hr, "restingHR": round(rhr) if _num(rhr) else None, "easyCeiling": easy,
            "walkAbove": easy + 5 if easy else None, "resumeBelow": easy - 10 if easy else None,
            "easyMethod": method, "thresholdHR": threshold, "zoneFloors": floors}


# ----------------------------------------------------------------- core series

def daily_load(days, acts):
    m = {d["date"]: 0.0 for d in days}
    for a in acts:
        if a["date"] in m:
            m[a["date"]] += a["load"]
    return [m[d["date"]] for d in days]


def baseline(days, key, upto=None, n=28):
    upto = len(days) if upto is None else upto
    return mean(d.get(key) for d in days[max(0, upto - n):upto])


def latest(days, key, within=None):
    for i in range(len(days) - 1, -1, -1):
        if within is not None and i < len(days) - within:
            return None
        if _num(days[i].get(key)):
            return i, days[i][key]
    return None


def is_hard(a, p75=None):
    hard_z = sum(z for z in a["zones"][3:] if _num(z))
    return (
        (_num(a["te"]) and a["te"] >= 3.5) or (_num(a["anTe"]) and a["anTe"] >= 2.5)
        or (a["dur"] >= 1200 and hard_z >= 0.2 * a["dur"]) or (p75 is not None and a["load"] >= p75 and a["load"] > 80)
    )


def pearson(xs, ys):
    pts = [(x, y) for x, y in zip(xs, ys) if _num(x) and _num(y)]
    if len(pts) < 12:
        return None
    mx, my = mean(p[0] for p in pts), mean(p[1] for p in pts)
    num = sum((x - mx) * (y - my) for x, y in pts)
    dx = sum((x - mx) ** 2 for x, _ in pts)
    dy = sum((y - my) ** 2 for _, y in pts)
    return (num / math.sqrt(dx * dy), len(pts)) if dx and dy else None


# ----------------------------------------------------------------- readiness

def readiness(days, load, flags=None):
    parts = []
    h = latest(days, "hrv", within=2)
    if h:
        i, v = h
        b, s = baseline(days, "hrv", i), sd(d.get("hrv") for d in days[max(0, i - 28):i])
        if b and s:
            parts.append({"name": "HRV", "score": clamp(70 + (v - b) / s * 15, 0, 100), "w": 0.3, "note": f"HRV {v:.0f} ms vs {b:.0f} avg"})
    r = latest(days, "rhr", within=2)
    if r:
        i, v = r
        b = baseline(days, "rhr", i)
        if b:
            parts.append({"name": "RHR", "score": clamp(75 - (v - b) * 7, 0, 100), "w": 0.2, "note": f"RHR {v:.0f} vs {b:.0f} avg"})
    sc, sl = latest(days, "sleepScore", within=2), latest(days, "sleepSec", within=2)
    if sc:
        parts.append({"name": "Sleep", "score": sc[1], "w": 0.25, "note": f"sleep score {sc[1]:.0f}"})
    elif sl:
        parts.append({"name": "Sleep", "score": clamp(sl[1] / 3600 / 8 * 100, 0, 100), "w": 0.25, "note": f"slept {hm(sl[1])}"})
    bb = latest(days, "bbHigh", within=2)
    if bb:
        parts.append({"name": "Body Battery", "score": bb[1], "w": 0.15, "note": f"Body Battery peak {bb[1]:.0f}"})
    ac, ch = mean(load[-7:]), mean(load[-28:])
    if ch and ch > 0:
        acwr = ac / ch
        parts.append({"name": "Load", "score": clamp(100 - max(0, acwr - 1) * 120, 0, 100), "w": 0.1, "note": f"load ratio {acwr:.2f}"})
    if not parts:
        return None
    w = sum(p["w"] for p in parts)
    score = round(sum(p["score"] * p["w"] for p in parts) / w)
    if flags and flags.get("illness"):
        score = min(score, 45)
    level = "good" if score >= 70 else "warning" if score >= 50 else "serious"
    verdict = {"good": "Ready to train", "warning": "Moderate: keep it easy to steady", "serious": "Low: prioritise recovery"}[level]
    return {"score": score, "level": level, "verdict": verdict, "parts": [{k: (round(v) if k == "score" else v) for k, v in p.items() if k != "w"} for p in parts]}


# ----------------------------------------------------------------- flags

def strain_flags(days):
    """Early-warning when several recovery markers move the wrong way together."""
    n = len(days)
    if n < 10:
        return {}
    recent = days[-2:]
    signs = []
    rb, hb, pb = baseline(days, "rhr", n - 2), baseline(days, "hrv", n - 2), baseline(days, "respiration", n - 2)
    r = mean(d.get("rhr") for d in recent)
    h = mean(d.get("hrv") for d in recent)
    p = mean(d.get("respiration") for d in recent)
    if r and rb and r - rb >= 3:
        signs.append(f"resting HR {signed(r - rb, 0)} bpm")
    if h and hb and h / hb - 1 <= -0.10:
        signs.append(f"HRV {signed((h / hb - 1) * 100, 0, '%')}")
    if p and pb and p - pb >= 1.0:
        signs.append(f"breathing rate {signed(p - pb, 1)} brpm")
    low_hrv_streak = 0
    for i in range(n - 1, -1, -1):
        b = baseline(days, "hrv", i)
        v = days[i].get("hrv")
        if not (_num(v) and b and v < b * 0.93):
            break
        low_hrv_streak += 1
    return {"illness": len(signs) >= 2, "signs": signs, "lowHrvStreak": low_hrv_streak}


# ----------------------------------------------------------------- insights

def _ins(level, title, text, tag, action=None):
    d = {"level": level, "title": title, "text": text, "tag": tag}
    if action:
        d["action"] = action
    return d


def health_insights(days, acts, flags, sets):
    out = []
    n7, n28 = days[-7:], days[-28:]
    if flags.get("illness"):
        out.append(_ins("serious", "Early strain warning",
                        f"Several recovery markers shifted together over the last two nights ({', '.join(flags['signs'])}). This pattern often shows up 1–2 days before a cold, after poor sleep, alcohol, travel or heavy training.",
                        "health", "Swap any hard session for rest or an easy walk, hydrate, and prioritise sleep until the numbers settle."))
    # RHR
    r7, r28 = mean(d.get("rhr") for d in n7), baseline(days, "rhr", len(days) - 7)
    if r7 and r28:
        diff = r7 - r28
        lvl = "serious" if diff >= 4 else "warning" if diff >= 2 else "good"
        txt = f"This week's resting HR averages {r7:.0f} bpm ({signed(diff, 1, ' bpm')} vs the previous 4 weeks)."
        txt += " Trending down: usually a sign of improving aerobic fitness." if diff <= -2 else " Elevated: a common sign of fatigue, stress or illness." if diff >= 2 else " Stable."
        out.append(_ins(lvl, "Resting heart rate", txt, "heart", "Back off intensity until it returns to baseline." if diff >= 2 else None))
    # HRV
    h7, h28 = mean(d.get("hrv") for d in n7), baseline(days, "hrv", len(days) - 7)
    if h7 and h28:
        pct = (h7 / h28 - 1) * 100
        lvl = "serious" if pct <= -10 else "warning" if pct <= -5 else "good"
        txt = f"7-day HRV {h7:.0f} ms is {signed(pct, 0, '%')} vs your prior 4-week average ({h28:.0f} ms)."
        hl, lo, hi = latest(days, "hrv"), latest(days, "hrvLow"), latest(days, "hrvHigh")
        if hl and lo and hi:
            v = hl[1]
            txt += (f" Last night ({v:.0f} ms) was below your balanced range of {lo[1]:.0f}–{hi[1]:.0f}." if v < lo[1]
                    else f" Last night ({v:.0f} ms) was above your balanced range." if v > hi[1]
                    else f" Last night ({v:.0f} ms) sat inside your balanced range.")
        if flags.get("lowHrvStreak", 0) >= 3:
            txt += f" It has been below baseline {flags['lowHrvStreak']} nights running."
        out.append(_ins(lvl, "Heart rate variability", txt, "recovery",
                        "Keep training easy and protect sleep for a few days." if lvl != "good" else None))
    # breathing / SpO2
    p7, p28 = mean(d.get("respiration") for d in n7), baseline(days, "respiration", len(days) - 7)
    if p7 and p28:
        diff = p7 - p28
        out.append(_ins("warning" if diff >= 1 else "good", "Breathing rate",
                        f"Overnight breathing averages {p7:.1f} breaths/min ({signed(diff, 1)} vs baseline).{' A sustained rise can signal illness or overreaching.' if diff >= 1 else ' Steady, a good sign.'}", "health"))
    s7 = mean(d.get("spo2") for d in n7)
    if s7:
        out.append(_ins("warning" if s7 < 94 else "good", "Blood oxygen",
                        f"Average SpO₂ {s7:.0f}% this week.{' Readings under ~94% at sea level are worth keeping an eye on; mention persistent lows to a doctor.' if s7 < 94 else ' Within the typical range.'}", "health"))
    # activity guideline
    wk = [(d.get("modMin") or 0) + 2 * (d.get("vigMin") or 0) for d in n7]
    if any(_num(d.get("modMin")) or _num(d.get("vigMin")) for d in n7):
        im = sum(wk)
        out.append(_ins("good" if im >= 150 else "warning", "Weekly activity target",
                        f"{im:.0f} intensity minutes in the last 7 days (vigorous counts double). WHO guidance is 150–300 for long-term health.",
                        "health", None if im >= 150 else f"Add about {150 - im:.0f} more minutes of brisk activity this week."))
    # VO2 & fitness age
    vs = [d["vo2"] for d in days if _num(d.get("vo2"))]
    if len(vs) >= 2:
        dv = vs[-1] - vs[0]
        fa = sets.get("fitnessAge", {}).get("data") if sets.get("fitnessAge", {}).get("ok") else None
        fa_txt = ""
        if isinstance(fa, dict):
            fage, cage = find(fa, ["fitnessAge"]), find(fa, ["chronologicalAge"])
            if _num(fage) and _num(cage):
                fa_txt = f" Garmin puts your fitness age at {fage:.0f} vs your actual {cage:.0f}."
        out.append(_ins("good" if dv >= 0 else "info", "Cardio fitness", f"VO₂ max is {vs[-1]:.1f} ({signed(dv, 1)} over this period).{fa_txt}", "fitness"))
    # weight
    ws = [(i, d["weight"]) for i, d in enumerate(days) if _num(d.get("weight"))]
    if len(ws) >= 3 and ws[-1][0] - ws[0][0] >= 14:
        per_wk = (ws[-1][1] - ws[0][1]) / ((ws[-1][0] - ws[0][0]) / 7)
        out.append(_ins("warning" if abs(per_wk) > 0.75 else "info", "Body weight",
                        f"{ws[-1][1]:.1f} kg, changing {signed(per_wk, 2, ' kg')}/week.{' Faster than ~0.5–0.75 kg/week can cost muscle and recovery.' if abs(per_wk) > 0.75 else ''}", "health"))
    return out


def wellbeing_insights(days, acts, load):
    out = []
    n7, n28 = days[-7:], days[-28:]
    s7, s28 = mean(d.get("sleepSec") for d in n7), mean(d.get("sleepSec") for d in n28)
    if s7:
        h = s7 / 3600
        var = sd(d["sleepSec"] / 3600 for d in n7 if _num(d.get("sleepSec")))
        debt = sum(max(0, 7.5 - d["sleepSec"] / 3600) for d in n7 if _num(d.get("sleepSec")))
        lvl = "good" if h >= 7 else "warning" if h >= 6.3 else "serious"
        txt = f"You averaged {hm(s7)} a night this week" + (f" (28-day: {hm(s28)})." if s28 else ".")
        if debt >= 2:
            txt += f" That adds up to about {debt:.1f} h short of 7.5 h/night."
        if var and var > 0.9:
            txt += f" Duration swings a lot night to night (±{var:.1f} h)."
        out.append(_ins(lvl, "Sleep duration", txt, "sleep",
                        f"Aim for lights-out ~{max(15, round(debt / 7 * 60 / 5) * 5)} min earlier for the next week." if debt >= 2 else None))
    deep = mean(d["deepSec"] / d["sleepSec"] for d in n28 if _num(d.get("deepSec")) and d.get("sleepSec"))
    rem = mean(d["remSec"] / d["sleepSec"] for d in n28 if _num(d.get("remSec")) and d.get("sleepSec"))
    if deep and rem:
        out.append(_ins("warning" if deep < 0.12 else "good", "Sleep quality",
                        f"Over 28 days {deep * 100:.0f}% of your sleep was deep and {rem * 100:.0f}% REM.{' Deep sleep is on the low side.' if deep < 0.12 else ' A healthy mix.'}",
                        "sleep", "Avoid hard workouts, alcohol and big meals within 3 h of bed." if deep < 0.12 else None))
    # schedule regularity
    starts = [d.get("sleepStartMin") for d in n28 if _num(d.get("sleepStartMin"))]
    if len(starts) >= 7:
        # unwrap around midnight: treat times before noon as after midnight
        unwrapped = [m + 1440 if m < 720 else m for m in starts]
        spread = sd(unwrapped)
        avg = mean(unwrapped) % 1440
        wkday = mean(((d["sleepStartMin"] + 1440) if d["sleepStartMin"] < 720 else d["sleepStartMin"]) for d in n28 if _num(d.get("sleepStartMin")) and date.fromisoformat(d["date"]).weekday() < 4)
        wkend = mean(((d["sleepStartMin"] + 1440) if d["sleepStartMin"] < 720 else d["sleepStartMin"]) for d in n28 if _num(d.get("sleepStartMin")) and date.fromisoformat(d["date"]).weekday() in (5, 6))
        txt = f"Typical bedtime ~{int(avg // 60):02d}:{int(avg % 60):02d}, varying by ±{spread:.0f} min."
        if wkday and wkend and abs(wkend - wkday) >= 60:
            txt += f" Weekend bedtimes shift {abs(wkend - wkday) / 60:.1f} h, a 'social jet lag' that can leave Mondays rough."
        out.append(_ins("good" if spread <= 45 else "warning", "Sleep schedule", txt, "sleep",
                        "Keep bed and wake times within ~30 min every day, weekends included." if spread > 45 else None))
    # stress
    t7, t28 = mean(d.get("stress") for d in n7), baseline(days, "stress", len(days) - 7)
    if t7:
        wd = mean(d.get("stress") for d in n28 if date.fromisoformat(d["date"]).weekday() < 5)
        we = mean(d.get("stress") for d in n28 if date.fromisoformat(d["date"]).weekday() >= 5)
        txt = f"Average all-day stress is {t7:.0f}" + (f" (prior 4 weeks: {t28:.0f})." if t28 else ".")
        if wd and we and wd - we >= 6:
            txt += f" Workdays run noticeably higher ({wd:.0f} vs {we:.0f} on weekends)."
        out.append(_ins("serious" if t7 > 50 else "warning" if t7 > 35 else "good", "Stress", txt, "mind",
                        "Try 2–3 short breathing breaks (5 min) during the workday and a screen-free last hour before bed." if t7 > 35 else None))
    # body battery
    peaks = [d.get("bbHigh") for d in n7 if _num(d.get("bbHigh"))]
    if peaks:
        lows = sum(1 for p in peaks if p < 50)
        net = mean((d.get("bbCharged") or 0) - (d.get("bbDrained") or 0) for d in n7 if _num(d.get("bbCharged")) and _num(d.get("bbDrained")))
        txt = f"Body Battery peaked at {mean(peaks):.0f} on average this week"
        txt += f", and {lows} of {len(peaks)} days never got above 50." if lows else "."
        if net is not None:
            txt += f" Daily charge minus drain averages {signed(net, 0)}."
        out.append(_ins("warning" if lows >= 3 or (net is not None and net < -10) else "good", "Energy", txt, "energy",
                        "You're spending more than you recharge: add a rest day or an early night." if lows >= 3 else None))
    # movement
    st7 = mean(d.get("steps") for d in n7[:-1])
    if st7:
        goal = (latest(days, "stepGoal") or (0, 10000))[1]
        hit = sum(1 for d in n28[:-1] if _num(d.get("steps")) and d["steps"] >= goal)
        out.append(_ins("good" if st7 >= goal else "info" if st7 >= goal * 0.7 else "warning", "Daily movement",
                        f"{st7:,.0f} steps/day over the last 7 full days; goal of {goal:,.0f} hit on {hit} of the last {max(0, len(n28) - 1)} days.", "activity",
                        "A 15-minute walk after lunch adds ~1,800 steps." if st7 < goal else None))
    # rest days
    trained = {a["date"] for a in acts}
    streak = 0
    for d in reversed(days[:-1]):
        if d["date"] in trained:
            streak += 1
        else:
            break
    rest14 = sum(1 for d in days[-14:] if d["date"] not in trained)
    if streak >= 6 or (acts and rest14 < 2):
        out.append(_ins("warning", "Rest days", f"{streak} training days in a row, with {rest14} rest day(s) in the last two weeks.", "recovery",
                        "Schedule at least one full rest day per week; adaptation happens during recovery."))
    # patterns
    c = pearson([d["sleepSec"] / 3600 if _num(d.get("sleepSec")) else None for d in days], [d.get("hrv") for d in days])
    if c and abs(c[0]) >= 0.3:
        out.append(_ins("info", "Sleep drives your HRV",
                        f"Across {c[1]} nights, longer sleep and higher HRV move together (r = {c[0]:.2f}). Sleep is one of your strongest recovery levers.", "pattern"))
    late = {a["date"] for a in acts if _num(a.get("hour")) and a["hour"] >= 19 and a["load"] > 40}
    nxt = {d["date"]: days[i + 1].get("sleepScore") for i, d in enumerate(days[:-1])}
    late_scores = [nxt[d] for d in late if _num(nxt.get(d))]
    other = [nxt[d["date"]] for d in days[:-1] if d["date"] not in late and _num(nxt.get(d["date"]))]
    if len(late_scores) >= 4 and other:
        diff = mean(late_scores) - mean(other)
        if abs(diff) >= 3:
            out.append(_ins("info" if diff < 0 else "good", "Evening workouts and sleep",
                            f"After workouts that start at 19:00 or later, your sleep score averages {mean(late_scores):.0f} vs {mean(other):.0f} otherwise ({signed(diff, 0)}).",
                            "pattern", "Move hard sessions earlier in the day when you can." if diff < 0 else None))
    return out


def training_insights(days, acts, load):
    out = []
    ac, ch = mean(load[-7:]), mean(load[-28:])
    if ch and ch > 0:
        r = ac / ch
        lvl = "serious" if r > 1.5 else "warning" if r > 1.3 else "info" if r < 0.8 else "good"
        msg = ("a sharp spike; injury risk rises when load jumps this fast." if r > 1.5
               else "building quickly. Fine for a short block, but plan a lighter week soon." if r > 1.3
               else "lighter than usual: good for a recovery week, but fitness slips if it lasts more than 2–3 weeks." if r < 0.8
               else "in the productive range (0.8–1.3).")
        out.append(_ins(lvl, "Training load balance", f"Acute (7-day) vs chronic (28-day) load ratio is {r:.2f}, {msg}", "training",
                        "Keep the next 3–4 days easy." if r > 1.5 else None))
    if acts:
        start28 = days[-28]["date"] if len(days) >= 28 else days[0]["date"]
        recent = [a for a in acts if a["date"] >= start28]
        d28 = len({a["date"] for a in recent})
        hrs = sum(a["dur"] for a in recent) / 3600
        txt = f"You trained on {d28} of the last 28 days ({d28 / 4:.1f} days/week), {hrs:.1f} h total."
        if len(days) >= 56:
            prev = [a for a in acts if days[-56]["date"] <= a["date"] < start28]
            ph = sum(a["dur"] for a in prev) / 3600
            if ph:
                txt += f" That's {signed((hrs / ph - 1) * 100, 0, '%')} volume vs the 4 weeks before."
        out.append(_ins("good" if d28 >= 12 else "info" if d28 >= 8 else "warning", "Consistency", txt, "training"))
    z = [0.0] * 5
    for a in acts:
        for i, v in enumerate(a["zones"]):
            if _num(v):
                z[i] += v
    zt = sum(z)
    if zt > 3600:
        easy, hard = (z[0] + z[1]) / zt, (z[3] + z[4]) / zt
        out.append(_ins("good" if easy >= 0.7 else "warning", "Intensity distribution",
                        f"{easy * 100:.0f}% of training time was in zones 1–2 and {hard * 100:.0f}% in zones 4–5.",
                        "training", None if easy >= 0.7 else "Make most sessions truly easy (conversational); keep 1–2 hard sessions a week."))
    nxt = [days[i + 1].get("hrv") if i + 1 < len(days) else None for i in range(len(days))]
    loads = sorted(v for v in load if v > 0)
    if len(loads) >= 8:
        q = loads[int(len(loads) * 0.75)]
        after = mean(nxt[i] for i, v in enumerate(load) if v >= q)
        other = mean(nxt[i] for i, v in enumerate(load) if v < q)
        if after and other:
            pct = (after / other - 1) * 100
            out.append(_ins("info" if pct < -5 else "good", "How hard days affect you",
                            f"After your hardest days (top 25% by load), overnight HRV averaged {after:.0f} ms vs {other:.0f} ms otherwise ({signed(pct, 0, '%')}).",
                            "pattern", "Follow big sessions with an easy or rest day." if pct < -8 else None))
    return out


# ----------------------------------------------------------------- running

def _vo2_at(v):  # Daniels/Gilbert: oxygen cost at velocity v (m/min)
    return -4.60 + 0.182258 * v + 0.000104 * v * v


def _pct_max(t):  # fraction of VO2max sustainable for t minutes
    return 0.8 + 0.1894393 * math.exp(-0.012778 * t) + 0.2989558 * math.exp(-0.1932605 * t)


def vdot_from(dist_m, minutes):
    if minutes <= 0:
        return None
    return _vo2_at(dist_m / minutes) / _pct_max(minutes)


def velocity_at(vo2):
    a, b, c = 0.000104, 0.182258, -4.60 - vo2
    return (-b + math.sqrt(b * b - 4 * a * c)) / (2 * a)


def predict_time(vdot, dist_m):
    lo, hi = dist_m / 500, dist_m / 60  # minutes
    for _ in range(60):
        mid = (lo + hi) / 2
        if _vo2_at(dist_m / mid) > vdot * _pct_max(mid):
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


BEST_LABELS = {"1k": "1K", "3k": "3K", "5k": "5K", "10k": "10K", "21.1k": "Half marathon"}
BEST_EFFORT_HR = {"3k": 0.86, "5k": 0.86, "10k": 0.84, "21.1k": 0.82}


def _ok(d):
    return d if d and not d.get("error") else None


def run_kind(a, d, hrp, median_km=None):
    """easy / long / tempo / intervals / race, from the run's own samples when available."""
    name = (a.get("name") or "").lower()
    km = (a.get("dist") or 0) / 1000
    if any(w in name for w in ("race", "parkrun", "marathon", " 10k", " 5k")) and d and d.get("steady"):
        return "race"
    if d and d.get("intervals"):
        return "intervals"
    if km >= max(12, 1.4 * (median_km or 0)):
        return "long"
    if d and d.get("steady") and hrp.get("thresholdHR"):
        above = run_detail.time_above(d, hrp["thresholdHR"] - 3)
        if above is not None and above >= 0.35:
            return "tempo"
    if not d and _num(a.get("te")) and a["te"] >= 3.5:
        return "tempo"
    return "easy"


def describe_run(a, d, hrp, fitness=None, kind=None, pbs=None):
    """A run's headline numbers plus short, plain-language notes. Includes chart series when present."""
    d = _ok(d)
    out = {"id": a["id"], "date": a["date"], "name": a["name"], "kind": kind or run_kind(a, d, hrp),
           "distKm": round((a.get("dist") or 0) / 1000, 2), "dur": a["dur"], "avgHR": a.get("avgHR"), "hasDetail": bool(d)}
    if a.get("dist"):
        out["paceSec"] = round(a["dur"] / (a["dist"] / 1000))
    notes = []
    if d:
        out.update({k: d.get(k) for k in ("gapSec", "paceSec", "elevGain", "cadence", "decoupling", "halves", "fadePct", "intervals", "best", "splits", "series", "paceCv", "movingSec")})
        if d.get("gapSec") and d.get("paceSec") and d["paceSec"] - d["gapSec"] >= 8:
            notes.append(("info", f"Hilly: {d['elevGain']} m of climbing. Hill-adjusted pace {pace_str(d['gapSec'])} vs {pace_str(d['paceSec'])} actual."))
        iv = d.get("intervals")
        if iv:
            txt = f"{iv['reps']} × {dur_str(iv['repSec'] / 60)} reps at {pace_str(iv['paceSec'])} average, consistency ±{iv['cvPct']:.1f}%"
            txt += f", last rep {signed(iv['fadePct'], 1, '%')} vs first." if abs(iv["fadePct"]) >= 1 else ", even from first to last."
            lvl = "good" if iv["cvPct"] <= 4 and iv["fadePct"] <= 3 else "warning"
            if fitness and fitness.get("intervalSec"):
                diff = (iv["paceSec"] / fitness["intervalSec"] - 1) * 100
                if diff <= -4:
                    txt += f" That's {abs(diff):.0f}% faster than your interval pace: either fitness is higher than estimated or the reps were run too hard."
                elif diff >= 5:
                    txt += f" That's {diff:.0f}% slower than your interval pace; fine if they were hill or longer reps."
            if iv.get("hrWork") and iv.get("hrRest"):
                txt += f" HR {iv['hrWork']} working, {iv['hrRest']} recovering."
            notes.append((lvl, txt))
        h = d.get("halves")
        if h and out["kind"] in ("long", "race", "tempo", "easy") and out["distKm"] >= 5:
            if h["pct"] <= -1.5:
                notes.append(("good", f"Negative split: second half {abs(h['pct']):.1f}% faster (hill-adjusted)."))
            elif h["pct"] >= 5:
                notes.append(("warning", f"Slowed {h['pct']:.1f}% in the second half (hill-adjusted). Start a little easier next time."))
            else:
                notes.append(("good", f"Even pacing: halves within {abs(h['pct']):.1f}%."))
        if d.get("decoupling") is not None:
            dc = d["decoupling"]
            notes.append(("good" if dc < 5 else "warning" if dc < 10 else "serious",
                          f"Heart-rate drift {dc:.1f}%" + (": solid aerobic endurance at this pace." if dc < 5 else ": HR rose noticeably for the same pace. Heat, fuelling or aerobic base." if dc < 10 else ": large drift, so this pace is beyond your current aerobic endurance for this duration.")))
        if out["kind"] == "easy" and hrp.get("easyCeiling"):
            above = run_detail.time_above(d, hrp["easyCeiling"] + 1)
            if above is not None:
                out["aboveEasyPct"] = round(above * 100)
                if above > 0.25:
                    notes.append(("warning", f"{above * 100:.0f}% of this easy run was above your easy ceiling ({hrp['easyCeiling']:.0f} bpm)."))
        cap = hrp.get("easyCeiling")
        if cap and _num(d.get("hr5")) and d["hr5"] > cap:
            notes.append(("warning", f"Started hot: {d['hr5']} bpm at minute 5" + (f" and {d['hr10']} at minute 10" if _num(d.get("hr10")) else "") + ". Warm up with a 5 min walk and 5 min very slow jog."))
        if _num(d.get("fadePct")) and d["fadePct"] <= -5:
            notes.append(("warning", f"Fast finish: the last stretch was {abs(d['fadePct']):.0f}% quicker than your average pace, which drives HR to its peak."))
        if _num(d.get("tempC")) and d["tempC"] >= 28:
            notes.append(("info", f"Hot run: watch logged ~{d['tempC']}°C. Heat typically adds 10–20 bpm."))
        sa = run_detail.speed_at_hr(d, cap) if cap else None
        if sa and sa[0] > 0.5:
            out["capPaceSec"] = round(1000 / sa[0])
            notes.append(("info", f"At ~{cap} bpm you moved at {pace_str(1000 / sa[0])} ({sa[1] / 60:.0f} min of data, walking included)."))
        out["tempC"] = d.get("tempC")
        out["hr5"] = d.get("hr5")
        for k, be in (d.get("best") or {}).items():
            if pbs and k in pbs and pbs[k]["id"] == a["id"] and k != "1k":
                notes.append(("good", f"Fastest {BEST_LABELS[k]} of the last 90 days: {dur_str(be['sec'] / 60)}."))
    out["notes"] = [{"level": lv, "text": t} for lv, t in notes]
    return out


RATIO_LADDER = [  # (max share of time above the walk trigger, ratio label, jog min, walk min, easy minutes, long minutes)
    (0.05, "continuous", None, None, "45–50", 80),
    (0.15, "5:1", 5, 1, "45", 70),
    (0.30, "3:1", 3, 1, "40–45", 60),
    (1.01, "2:1", 2, 1, "35–40", 50),
]


def heat_status(runs, det, weather=None):
    """Recent running temperatures (watch sensor) and, if configured, today's forecast."""
    temps = []
    for r in runs[:6]:
        d = det.get(r["id"])
        t = (d or {}).get("tempC")
        if not _num(t):
            t = r.get("tempMax")
        if _num(t):
            temps.append(t)
    out = {"recentC": round(statistics.median(temps)) if temps else None, "runs": len(temps)}
    out["hot"] = bool(temps) and statistics.median(temps) >= 27
    if weather:
        out["forecast"] = weather
        if _num(weather.get("maxFeelsC")) and weather["maxFeelsC"] >= 32:
            out["hot"] = True
    return out


def base_plan(runs, det, kinds, hrp, today, readiness_res, flags, heat):
    """Heart-rate-capped base building with adaptive run-walk ratios."""
    cap, walk, resume = hrp.get("easyCeiling"), hrp.get("walkAbove"), hrp.get("resumeBelow")
    if not cap:
        return None
    recent = [det[r["id"]] for r in runs[:3] if det.get(r["id"])]
    shares = [x for x in (run_detail.time_above(d, walk + 0.5) for d in recent) if x is not None]
    above = mean(shares) if shares else None
    first = min(date.fromisoformat(r["date"]) for r in runs)
    history_days = (today - first).days
    if above is None:
        level = RATIO_LADDER[-1]
    else:
        level = next(l for l in RATIO_LADDER if above < l[0])
    active = history_days < 70 or (above is not None and above >= 0.15)
    quality_ok = (above is not None and above < 0.15 and history_days >= 42 and not flags.get("illness")
                  and (readiness_res or {}).get("score", 0) >= 70)
    label, jog, wlk, easy_min, long_min = level[1], level[2], level[3], level[4], level[5]
    pattern = "run continuously" if jog is None else f"jog {jog} min / walk {wlk} min"
    rules = [
        f"Run by heart rate, not pace: stay at or under {cap} bpm.",
        f"When HR reaches {walk}, walk briskly until it drops below {resume}, then jog again.",
        "Warm up every time: 5 min brisk walk + 5 min very slow jog.",
        "Slow jog = short, quick steps (about 165–175 per minute), feet low. Pace doesn't matter.",
        "No fast finishes" + ("." if quality_ok else ", and no hard sessions until your heart rate stays under the cap."),
    ]
    if heat.get("hot"):
        rules.append("Heat: run before 06:00 or on a treadmill; drink 400–600 ml 1–2 h before; carry electrolytes beyond 45 min.")
    return {
        "active": active, "cap": cap, "walkAbove": walk, "resumeBelow": resume, "method": hrp.get("easyMethod"),
        "ratio": label, "pattern": pattern, "jog": jog, "walk": wlk, "easyMin": easy_min, "longMin": long_min,
        "aboveCapPct": round(above * 100) if above is not None else None, "basedOnRuns": len(shares),
        "historyDays": history_days, "qualityAllowed": quality_ok, "rules": rules,
        "next": None if label == "continuous" else next(l[1] for l in reversed(RATIO_LADDER[:RATIO_LADDER.index(level)])) if RATIO_LADDER.index(level) > 0 else None,
    }


def running_report(days, acts, load, hrp, readiness_res, flags, today: date, details=None, weather=None):
    runs = [a for a in acts if a["family"] == "run" and a["dur"] > 0]
    if not runs:
        return None
    details = details or {}
    det = {r["id"]: _ok(details.get(str(r["id"]))) for r in runs}
    km_list = sorted((r["dist"] or 0) / 1000 for r in runs[:30])
    median_km = km_list[len(km_list) // 2] if km_list else 0
    kinds = {r["id"]: run_kind(r, det[r["id"]], hrp, median_km) for r in runs}
    rec = {}
    # weekly volume (Mon–Sun), last 12 weeks
    def wk(d):
        x = date.fromisoformat(d)
        return (x - timedelta(days=x.weekday())).isoformat()
    this_week = wk(today.isoformat())
    weeks = sorted({wk((today - timedelta(weeks=i)).isoformat()) for i in range(12)})
    vol = {w: {"week": w, "km": 0.0, "runs": 0, "longKm": 0.0, "hours": 0.0} for w in weeks}
    for r in runs:
        w = wk(r["date"])
        if w in vol:
            km = (r["dist"] or 0) / 1000
            vol[w]["km"] += km
            vol[w]["runs"] += 1
            vol[w]["hours"] += r["dur"] / 3600
            vol[w]["longKm"] = max(vol[w]["longKm"], km)
    weekly = [vol[w] for w in weeks]
    for w in weekly:
        w["km"], w["longKm"], w["hours"] = round(w["km"], 1), round(w["longKm"], 1), round(w["hours"], 2)
    done = [w for w in weekly if w["week"] < this_week]
    last3 = [w["km"] for w in done[-3:]]
    base_km = mean(last3) or 0
    last_km = done[-1]["km"] if done else 0
    rec["weekly"] = weekly

    # fitness: VDOT from recent hard efforts, else Garmin VO2 max
    hard_hr = (hrp.get("maxHR") or 0) * 0.85
    cutoff = (today - timedelta(days=90)).isoformat()
    cands = []
    for r in runs:
        if r["date"] < cutoff or not r["dist"] or r["dist"] < 3000 or r["dur"] < 720:
            continue
        v = vdot_from(r["dist"], r["dur"] / 60)
        if v and 20 <= v <= 85:
            effort = (_num(r["avgHR"]) and hard_hr and r["avgHR"] >= hard_hr) or (_num(r["te"]) and r["te"] >= 3.5)
            cands.append((v, effort, r))
    # best efforts inside runs (from the activity files) beat whole-run averages
    max_hr = hrp.get("maxHR") or 0
    for r in runs:
        d = det[r["id"]]
        if r["date"] < cutoff or not d:
            continue
        for k, be in (d.get("best") or {}).items():
            if k not in BEST_EFFORT_HR:
                continue
            dist_m = run_detail.BEST_DISTANCES[k]
            v = vdot_from(dist_m, be["sec"] / 60)
            if not v or not 20 <= v <= 85:
                continue
            effort = (max_hr and be.get("hr") and be["hr"] >= max_hr * BEST_EFFORT_HR[k]) or kinds[r["id"]] == "race"
            cands.append((v, effort, {**r, "dist": dist_m, "dur": be["sec"], "_segment": BEST_LABELS[k]}))
        iv = d.get("intervals")
        if iv and iv["reps"] >= 4 and 150 <= iv["repSec"] <= 360 and iv["cvPct"] <= 4 and iv["fadePct"] <= 3:
            # even 2.5-6 min reps are run at roughly VO2max speed; treat that as a conservative calibration
            v = _vo2_at(1000 / iv["paceSec"] * 60)
            if 20 <= v <= 85:
                cands.append((v, True, {**r, "_interval": iv}))
    hard = [c for c in cands if c[1]]
    garmin_vo2 = next((r["vo2"] for r in runs if _num(r.get("vo2"))), None) or (latest(days, "vo2") or (0, None))[1]
    if hard:
        vdot, _, best = max(hard, key=lambda c: c[0])
        src = (f"your fastest {best['_segment']} ({dur_str(best['dur'] / 60)}) inside the {best['date']} run" if best.get("_segment")
               else f"your {best['_interval']['reps']} × {dur_str(best['_interval']['repSec'] / 60)} interval session on {best['date']}" if best.get("_interval")
               else f"your {best['dist'] / 1000:.1f} km effort on {best['date']}")
    elif garmin_vo2:
        vdot, src = garmin_vo2 * 0.95, "Garmin VO₂ max (no recent hard run to calibrate)"
    elif cands:
        vdot, _, best = max(cands, key=lambda c: c[0])
        src = "your fastest recent run (likely conservative)"
    else:
        vdot = None
    if vdot:
        vdot = round(vdot, 1)
        def pace_at(frac):
            return 60000 / velocity_at(vdot * frac)  # sec per km
        m_time = predict_time(vdot, 42195)
        rec["fitness"] = {"vdot": vdot, "source": src, "intervalSec": round(pace_at(0.975)), "thresholdSec": round(pace_at(0.88)), "easySec": round(pace_at(0.70))}
        rec["paces"] = [
            {"zone": "Easy / long", "pace": f"{pace_str(pace_at(0.74))[:-3]}–{pace_str(pace_at(0.62))}", "hr": f"< {hrp['easyCeiling']:.0f} bpm" if hrp.get("easyCeiling") else "conversational", "use": "Most of your running"},
            {"zone": "Marathon", "pace": pace_str(m_time * 60 / 42.195), "hr": "", "use": "Long-run finishes, steady runs"},
            {"zone": "Threshold", "pace": pace_str(pace_at(0.88)), "hr": f"~{hrp['thresholdHR']:.0f} bpm" if hrp.get("thresholdHR") else "comfortably hard", "use": "Tempo, cruise intervals"},
            {"zone": "Interval", "pace": pace_str(pace_at(0.975)), "hr": "", "use": "3–5 min repeats (VO₂ max)"},
            {"zone": "Repetition", "pace": pace_str(pace_at(1.06)), "hr": "", "use": "200–400 m strides & reps"},
        ]
        rec["predictions"] = [{"race": n, "time": dur_str(predict_time(vdot, d))} for n, d in (("5K", 5000), ("10K", 10000), ("Half marathon", 21097.5), ("Marathon", 42195))]
        easy_lo = pace_at(0.74)
    else:
        easy_lo = None

    # best efforts (90 days) and a breakdown of recent runs
    pbs = {}
    for r in runs:
        d = det[r["id"]]
        if r["date"] < cutoff or not d:
            continue
        for k, be in (d.get("best") or {}).items():
            if k not in pbs or be["sec"] < pbs[k]["sec"]:
                pbs[k] = {"sec": be["sec"], "date": r["date"], "id": r["id"], "hr": be.get("hr")}
    rec["bests"] = [{"label": BEST_LABELS[k], "time": dur_str(v["sec"] / 60), "pace": pace_str(v["sec"] / run_detail.BEST_DISTANCES[k] * 1000), "date": v["date"], "hr": v["hr"]}
                    for k, v in sorted(pbs.items(), key=lambda kv: run_detail.BEST_DISTANCES[kv[0]])]
    rec["runs"] = [{k: v for k, v in describe_run(r, det[r["id"]], hrp, rec.get("fitness"), kinds[r["id"]], pbs).items() if k not in ("series", "splits", "best")}
                   for r in runs[:12]]
    rec["detailCoverage"] = {"withDetail": sum(1 for r in runs if r["date"] >= cutoff and det[r["id"]]), "runs": sum(1 for r in runs if r["date"] >= cutoff)}

    # next-week volume guidance
    building = len(done) >= 3 and done[-1]["km"] > done[-2]["km"] > done[-3]["km"] > 0
    acwr = (mean(load[-7:]) or 0) / (mean(load[-28:]) or 1)
    tired = flags.get("illness") or (readiness_res and readiness_res["score"] < 50) or acwr > 1.3
    if base_km < 5:
        target, why = (10, 15), "you're running little right now; rebuild gently with 3 short easy runs."
    elif building:
        target, why = (round(last_km * 0.7), round(last_km * 0.8)), "three building weeks in a row: take a lighter week to absorb the work."
    elif tired:
        target, why = (round(base_km * 0.8), round(base_km * 0.9)), "recovery markers or load ratio say hold back a little."
    else:
        target, why = (round(base_km), round(min(base_km * 1.1, last_km * 1.1 + 2))), "fine to build, up to ~10% over recent weeks."
    rec["nextWeek"] = {"kmLow": target[0], "kmHigh": max(target), "reason": why, "recentAvgKm": round(base_km, 1)}
    heat = heat_status(runs, det, weather)
    rec["heat"] = heat
    bp = base_plan(runs, det, kinds, hrp, today, readiness_res, flags, heat)
    rec["base"] = bp
    if bp and bp["active"] and base_km <= 20 and not tired:
        rec["nextWeek"] = {"kmLow": max(10, min(14, round(base_km))), "kmHigh": 18, "recentAvgKm": round(base_km, 1),
                           "reason": "base phase: hold distance steady and let time at an easy heart rate do the work; add at most 10% a week once the cap feels manageable."}

    # today's session
    last_run = runs[0]
    p75 = sorted(a["load"] for a in acts)[int(len(acts) * 0.75)] if len(acts) >= 4 else None
    def hard(a):
        k = kinds.get(a["id"])
        return k in ("intervals", "tempo", "race") if k and det.get(a["id"]) else is_hard(a, p75)
    since_hard = next(((today - date.fromisoformat(a["date"])).days for a in acts if hard(a)), 99)
    long_cut = max(8.0, base_km * 0.25)
    since_long = next(((today - date.fromisoformat(r["date"])).days for r in runs if (r["dist"] or 0) / 1000 >= long_cut), 99)
    ran_today = any(r["date"] == today.isoformat() for r in runs)
    score = readiness_res["score"] if readiness_res else 60
    weekend = today.weekday() >= 5
    runs_per_wk = round(mean(w["runs"] for w in done[-4:]) or 3)
    easy_pace = f"{pace_str(easy_lo)} or slower" if easy_lo else "conversational pace"
    hr_cap = f", HR under {hrp['easyCeiling']:.0f}" if hrp.get("easyCeiling") else ""
    longest = max((w["longKm"] for w in done[-6:]), default=0)
    if ran_today:
        sess = {"type": "done", "title": "Already ran today", "detail": f"{last_run['name']}: {(last_run['dist'] or 0) / 1000:.1f} km in {hm(last_run['dur'])}. Refuel, rehydrate and keep the rest of the day easy.", "level": "good"}
    elif flags.get("illness") or score < 45:
        sess = {"type": "rest", "title": "Rest or a gentle walk", "detail": "Recovery markers are well off baseline. A rest day now protects the rest of the week.", "level": "serious"}
    elif score < 60 or since_hard <= 1 or acwr > 1.4:
        mins = 30 if score < 55 else 40
        sess = {"type": "easy", "title": f"Easy run, {mins} min", "detail": f"Keep it conversational: {easy_pace}{hr_cap}. Skip it if legs feel heavy.", "level": "warning"}
    elif since_long >= 6 and (weekend or since_long >= 9) and score >= 60:
        last_long = next((r for r in runs if kinds[r["id"]] == "long" and det[r["id"]]), None)
        lld = det[last_long["id"]] if last_long else None
        grow = 1.5
        note = ""
        if lld and lld.get("decoupling") is not None:
            if lld["decoupling"] >= 7:
                grow = 0
                note = f" Your last long run drifted {lld['decoupling']:.0f}% in heart rate, so hold the distance, start slower and fuel every 30–40 min."
            elif lld["decoupling"] < 5:
                note = f" Last long run held steady (drift {lld['decoupling']:.0f}%), so a small step up is fine."
        if lld and lld.get("halves") and lld["halves"]["pct"] >= 5:
            note += " Last time you faded in the second half: run the first half deliberately slower."
        km = round(min(max(base_km * 0.3, 8), longest + grow if longest else 12), 0)
        sess = {"type": "long", "title": f"Long run, ~{km:.0f} km", "detail": f"Steady and easy ({easy_pace}{hr_cap}). Fuel if over 75 min; finish the last 10 min at marathon effort only if you feel strong.{note}", "level": "good"}
    elif score >= 70 and since_hard >= 2 and runs_per_wk >= 3:
        t_pace = next((p["pace"] for p in rec.get("paces", []) if p["zone"] == "Threshold"), "comfortably hard")
        i_pace = next((p["pace"] for p in rec.get("paces", []) if p["zone"] == "Interval"), "hard")
        last_q = next((a for a in runs if hard(a)), None)
        last_kind = kinds.get(last_q["id"]) if last_q else None
        if last_q and det.get(last_q["id"]):
            do_threshold = last_kind == "intervals"
        else:
            do_threshold = bool(last_q and _num(last_q.get("anTe")) and last_q["anTe"] >= 2.5)
        if do_threshold:
            sess = {"type": "threshold", "title": "Threshold session", "detail": f"15 min easy, then 3 × 8 min at {t_pace} with 2 min jog, 10 min easy cool-down.", "level": "good"}
        else:
            reps, rep_txt = 5, "3 min"
            prev = next((det[r["id"]]["intervals"] for r in runs if det[r["id"]] and det[r["id"]].get("intervals")), None)
            note = ""
            if prev:
                reps = prev["reps"]
                rep_txt = dur_str(prev["repSec"] / 60) if prev["repSec"] < 600 else "3 min"
                strong = prev["fadePct"] <= 3 and prev["cvPct"] <= 4
                if strong:
                    reps = min(8, reps + 1)
                    note = f" Last time you held {prev['reps']} reps evenly (±{prev['cvPct']:.1f}%), so add one."
                else:
                    note = f" Last time the reps faded {prev['fadePct']:.0f}%: repeat {prev['reps']} and start the first one a touch slower."
            sess = {"type": "intervals", "title": "VO₂ max intervals", "detail": f"15 min easy, then {reps} × {rep_txt} at {i_pace} with 2 min jog, 10 min easy cool-down.{note}", "level": "good"}
    else:
        sess = {"type": "easy", "title": "Easy aerobic run, 40–50 min", "detail": f"{easy_pace}{hr_cap}, then 4–6 × 20 s relaxed strides.", "level": "good"}
    sess["why"] = [x for x in [
        f"readiness {score}" if readiness_res else None,
        ("hard session today" if since_hard == 0 else f"last hard session {since_hard} day(s) ago") if since_hard < 99 else "no recent hard session",
        f"last long run {since_long} day(s) ago" if 0 < since_long < 99 else None,
        f"load ratio {acwr:.2f}",
    ] if x]
    if bp and bp["active"] and sess["type"] not in ("done", "rest"):
        warm = "5 min brisk walk + 5 min very slow jog, then "
        body = (f"{bp['pattern']}" if bp["jog"] else "run continuously")
        capline = f" Keep HR ≤ {bp['cap']}; at {bp['walkAbove']} walk until it's under {bp['resumeBelow']}. Short quick steps, no fast finish."
        fc = heat.get("forecast") or {}
        heatline = ((f" Coolest today around {fc['bestHour']} (feels {fc['bestFeelsC']}°C; up to {fc['maxFeelsC']}°C later)." if fc.get("bestHour") else " It's hot: go before 06:00 or use a treadmill.")
                    + " Drink 400–600 ml 1–2 h before.") if heat.get("hot") else ""
        weekend = today.weekday() >= 5
        long_done = any(date.fromisoformat(r["date"]) >= today - timedelta(days=today.weekday()) and (r["dur"] / 60) >= bp["longMin"] * 0.8 for r in runs)
        ran_yesterday = any(r["date"] == (today - timedelta(days=1)).isoformat() for r in runs)
        if weekend and not long_done:
            sess = {"type": "long", "title": f"Long run-walk, {bp['longMin']} min", "detail": warm + body + "." + capline + heatline, "level": "good"}
        elif ran_yesterday:
            sess = {"type": "rest", "title": "Rest or easy cross-training", "detail": "Back-to-back days add strain without much benefit in the base phase. Walk, cycle or swim easily if you want to move." + heatline, "level": "good"}
        else:
            strides = " Finish with 6 × 20 s relaxed strides (full recovery between)." if today.weekday() in (3, 4) else ""
            sess = {"type": "easy", "title": f"Easy run-walk, {bp['easyMin']} min", "detail": warm + body + "." + capline + strides + heatline, "level": "good"}
        sess["why"] = [x for x in [f"base phase ({bp['ratio']})",
                                   f"{bp['aboveCapPct']}% of recent running above {bp['walkAbove']} bpm" if bp["aboveCapPct"] is not None else None,
                                   f"readiness {score}" if readiness_res else None,
                                   f"recent runs at {heat['recentC']}°C" if heat.get("recentC") else None] if x]
    rec["today"] = sess

    # weekly plan outline, sized so the sessions add up to the suggested distance
    hi_km = rec["nextWeek"]["kmHigh"]
    n = int(clamp(runs_per_wk, 3, 6))
    while n > 2 and n * 4 > hi_km:
        n -= 1
    layout = {2: {2: "easy", 5: "long"},
              3: {1: "quality", 3: "easy", 5: "long"},
              4: {1: "quality", 2: "easy", 4: "easy", 6: "long"},
              5: {0: "easy", 1: "quality", 3: "easy", 4: "easy", 6: "long"},
              6: {0: "easy", 1: "quality", 2: "easy", 3: "quality", 4: "easy", 6: "long"}}[n]
    quality_ok = hi_km >= 20 and not tired
    long_km = max(5, round(min(hi_km * 0.32, (longest or hi_km * 0.32) + 1.5)))
    others = n - 1
    each = max(3, round(max(0, hi_km - long_km) / max(1, others)))
    if each >= long_km:  # the long run should stay the longest session
        long_km = each + 2
        each = max(3, round(max(0, hi_km - long_km) / max(1, others)))
    labels = {
        "quality": f"Quality ~{each} km incl. warm-up (threshold or intervals)" if quality_ok else f"Easy ~{each} km + 4–6 strides",
        "easy": f"Easy ~{each} km",
        "long": f"Long run ~{long_km} km",
    }
    names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    rec["planTotalKm"] = long_km + each * others
    rec["plan"] = [{"day": names[i], "type": (layout.get(i) if quality_ok or layout.get(i) != "quality" else "easy") or "rest",
                    "session": labels.get(layout.get(i), "Rest or easy cross-training")} for i in range(7)]

    if bp and bp["active"]:
        e, l = bp["easyMin"], bp["longMin"]
        rec["plan"] = [
            {"day": "Mon", "type": "rest", "session": "Rest"},
            {"day": "Tue", "type": "easy", "session": f"Easy run-walk {e} min (HR ≤ {bp['cap']})"},
            {"day": "Wed", "type": "rest", "session": "Rest or easy cross-training"},
            {"day": "Thu", "type": "easy", "session": f"Easy run-walk {e} min + 6 × 20 s strides"},
            {"day": "Fri", "type": "rest", "session": "Rest"},
            {"day": "Sat", "type": "long", "session": f"Long run-walk {l} min (HR ≤ {bp['cap']})"},
            {"day": "Sun", "type": "rest", "session": "Optional: easy 30 min, or cycling / swimming"},
        ]
        if bp["qualityAllowed"]:
            rec["plan"][3] = {"day": "Thu", "type": "quality", "session": "5 × 2 min faster, 2 min walk between (only if readiness is good)"}
        rec["planTotalKm"] = None

    # pace at the easy cap, run by run (walking included): the base-building progress test
    cap = hrp.get("easyCeiling")
    trend = []
    for r in reversed(runs):
        d = det.get(r["id"])
        sa = run_detail.speed_at_hr(d, cap) if d and cap else None
        if sa and sa[0] > 0.5:
            trend.append({"date": r["date"], "paceSec": round(1000 / sa[0]), "minutes": round(sa[1] / 60, 1)})
    rec["capTrend"] = trend

    # running-specific insights
    ins = []
    if len(done) >= 2 and done[-2]["km"] > 0:
        jump = (done[-1]["km"] / done[-2]["km"] - 1) * 100
        if jump > 30 and done[-1]["km"] - done[-2]["km"] > 5:
            ins.append(_ins("serious", "Mileage jump", f"Last week's {done[-1]['km']:.0f} km was {signed(jump, 0, '%')} on the week before. Big jumps are the most common route to running injuries.", "running", "Don't add more this week; settle back toward your recent average."))
    if done and done[-1]["km"] > 0 and done[-1]["longKm"] / done[-1]["km"] > 0.4 and done[-1]["runs"] >= 2:
        ins.append(_ins("warning", "Long run share", f"Your long run was {done[-1]['longKm'] / done[-1]['km'] * 100:.0f}% of last week's distance.", "running", "Keep the long run near 25–35% of weekly volume by adding short easy runs."))
    recent28 = [r for r in runs if r["date"] >= (today - timedelta(days=28)).isoformat()]
    easy_det = [det[r["id"]] for r in recent28 if kinds[r["id"]] == "easy" and det[r["id"]]]
    if hrp.get("easyCeiling") and len(easy_det) >= 3:
        shares = [x for x in (run_detail.time_above(d, hrp["easyCeiling"] + 1) for d in easy_det) if x is not None]
        if shares:
            avg = mean(shares)
            ins.append(_ins("warning" if avg > 0.25 else "good", "Easy-run discipline",
                            f"On your last {len(shares)} easy runs, {avg * 100:.0f}% of the time was above your easy ceiling ({hrp['easyCeiling']:.0f} bpm), measured second by second.",
                            "running", "Slow easy days down until most of the run sits under the ceiling; walk steep hills." if avg > 0.25 else None))
    elif hrp.get("easyCeiling"):
        recent_runs = [r for r in runs if r["date"] >= (today - timedelta(days=28)).isoformat() and _num(r["avgHR"])]
        easyish = [r for r in recent_runs if not is_hard(r, p75)]
        if len(easyish) >= 4:
            over = sum(1 for r in easyish if r["avgHR"] > hrp["easyCeiling"])
            share = over / len(easyish)
            ins.append(_ins("warning" if share > 0.4 else "good", "Easy-run discipline",
                            f"{over} of {len(easyish)} non-workout runs in the last 4 weeks averaged above your easy ceiling ({hrp['easyCeiling']:.0f} bpm).",
                            "running", "Slow easy days down; walk the hills if needed. Easy means truly easy." if share > 0.4 else None))
    cads = [r["cadence"] for r in runs[:15] if _num(r.get("cadence"))]
    if len(cads) >= 3:
        c = mean(cads)
        ins.append(_ins("info" if c < 160 else "good", "Cadence", f"Average cadence {c:.0f} steps/min over recent runs.", "running",
                        "Try a 5% higher step rate on easy runs (a metronome helps); it often reduces overstriding." if c < 160 else None))
    ef = []
    for r in runs:
        if kinds[r["id"]] not in ("easy", "long") or not _num(r["avgHR"]) or r["dur"] <= 600 or not r["dist"]:
            continue
        d = det[r["id"]]
        speed = 1000 / d["gapSec"] if d and d.get("gapSec") else r["dist"] / r["dur"]  # hill-adjusted when we can
        hr_ = d["avgHR"] if d and d.get("avgHR") else r["avgHR"]
        ef.append((speed * 60 / hr_, r["date"]))
    if len(ef) >= 8:
        ef.sort(key=lambda x: x[1])
        half = len(ef) // 2
        a, b = mean(x[0] for x in ef[:half]), mean(x[0] for x in ef[half:])
        pct = (b / a - 1) * 100
        ins.append(_ins("good" if pct >= 1 else "info" if pct > -2 else "warning", "Aerobic efficiency",
                        f"Speed per heartbeat on easy runs changed {signed(pct, 1, '%')} from the first to the second half of this period{': your aerobic engine is improving.' if pct >= 1 else '.'}", "running"))
    consec = 0
    rdays = {r["date"] for r in runs}
    d = today - timedelta(days=1)
    while d.isoformat() in rdays:
        consec += 1
        d -= timedelta(days=1)
    if consec >= 5:
        ins.append(_ins("warning", "Running streak", f"{consec} running days in a row.", "running", "Insert a rest or cross-training day to let tendons and bones adapt."))
    # heart-rate drift on steady runs of 45 min+
    drifts = [(r, det[r["id"]]["decoupling"]) for r in runs[:20] if det[r["id"]] and det[r["id"]].get("decoupling") is not None and det[r["id"]]["movingSec"] >= 2700][:5]
    if drifts:
        avg = mean(x for _, x in drifts)
        ins.append(_ins("good" if avg < 5 else "warning", "Aerobic endurance",
                        f"Across your last {len(drifts)} steady runs of 45 min or more, heart rate drifted {avg:.1f}% on average for the same (hill-adjusted) pace." +
                        (" Under 5% means your aerobic base comfortably supports these runs." if avg < 5 else " Above 5% suggests the aerobic base, fuelling or heat is limiting you on longer runs."),
                        "running", None if avg < 5 else "Keep long runs genuinely easy, take carbs on runs over 75 min, and add easy volume gradually."))
    # interval execution
    last_iv = next(((r, det[r["id"]]["intervals"]) for r in runs if det[r["id"]] and det[r["id"]].get("intervals")), None)
    if last_iv:
        r, iv = last_iv
        target = (rec.get("fitness") or {}).get("intervalSec")
        txt = f"{r['date']}: {iv['reps']} × {dur_str(iv['repSec'] / 60)} at {pace_str(iv['paceSec'])} (±{iv['cvPct']:.1f}% between reps, last rep {signed(iv['fadePct'], 1, '%')} vs first)."
        good = iv["cvPct"] <= 4 and iv["fadePct"] <= 3
        if target:
            diff = (iv["paceSec"] / target - 1) * 100
            txt += f" Target interval pace is {pace_str(target)} ({signed(diff, 0, '%')})."
            if diff <= -4:
                good = False
        ins.append(_ins("good" if good else "warning", "Last interval session", txt, "running",
                        None if good else "Aim for even reps at target pace; the last rep should feel as controlled as the first."))
    # pacing on long runs
    longs = [det[r["id"]] for r in recent28 if kinds[r["id"]] == "long" and det[r["id"]] and det[r["id"]].get("halves")]
    if longs:
        fades = [d["halves"]["pct"] for d in longs]
        bad = sum(1 for x in fades if x >= 5)
        ins.append(_ins("warning" if bad else "good", "Long-run pacing",
                        f"{len(longs)} long run(s) in the last 4 weeks; second half {signed(mean(fades), 1, '%')} vs first on average (hill-adjusted).",
                        "running", "Start long runs 10–15 s/km slower than feels natural and fuel early." if bad else None))
    if heat.get("hot"):
        ins.append(_ins("warning", "Heat is raising your heart rate",
                        f"Your watch logged a median {heat['recentC']}°C on your last {heat['runs']} runs. Heat and humidity typically add 10–20 bpm at the same pace and make HR creep up the whole run.",
                        "running", "Run before 06:00 or on a treadmill, pre-hydrate (400–600 ml 1–2 h before), and expect to walk more on hot days."))
    if cap:
        if len(trend) >= 2:
            a_, b_ = trend[0], trend[-1]
            diff = a_["paceSec"] - b_["paceSec"]
            ins.append(_ins("good" if diff > 5 else "info", f"Pace at {cap} bpm",
                            f"At about {cap} bpm you moved at {pace_str(b_['paceSec'])} on {b_['date']} vs {pace_str(a_['paceSec'])} on {a_['date']} (walking included). This is your base-building progress test; expect it to get faster over 6–12 weeks.",
                            "running"))
        else:
            ins.append(_ins("info", f"Pace at {cap} bpm",
                            f"Not enough time spent near {cap} bpm yet to measure. Your first capped run-walks will set the starting point.", "running"))
    hot_starts = [det[r["id"]]["hr5"] for r in runs[:5] if det.get(r["id"]) and _num(det[r["id"]].get("hr5"))]
    if cap and len(hot_starts) >= 3 and mean(hot_starts) > cap:
        ins.append(_ins("warning", "Starting too fast",
                        f"Five minutes into your recent runs your HR averaged {mean(hot_starts):.0f} bpm, already above your {cap} bpm cap.",
                        "running", "Walk briskly for 5 min, then jog very slowly for 5 min before settling in."))
    finishes = [det[r["id"]]["fadePct"] for r in runs[:6] if det.get(r["id"]) and _num(det[r["id"]].get("fadePct"))]
    fast_fin = sum(1 for f in finishes if f <= -5)
    if fast_fin >= 2:
        ins.append(_ins("warning", "Fast finishes",
                        f"{fast_fin} of your last {len(finishes)} runs ended with the final stretch 5%+ faster than average, pushing HR to its peak.",
                        "running", "Save fast finishes for one planned session a week, after the base phase."))
    rec["insights"] = ins
    rec["summary"] = {"runs28": sum(1 for r in runs if r["date"] >= (today - timedelta(days=28)).isoformat()),
                      "km28": round(sum((r["dist"] or 0) for r in runs if r["date"] >= (today - timedelta(days=28)).isoformat()) / 1000, 1),
                      "longest28": round(max(((r["dist"] or 0) / 1000 for r in runs if r["date"] >= (today - timedelta(days=28)).isoformat()), default=0), 1)}
    return rec


# ----------------------------------------------------------------- day report

def day_report(days, acts, load, target: str):
    idx = next((i for i, d in enumerate(days) if d["date"] == target), None)
    if idx is None:
        return None
    d = days[idx]
    cmp = {}
    for k in ("sleepSec", "sleepScore", "hrv", "rhr", "stress", "bbHigh", "steps", "respiration"):
        b = baseline(days, k, idx)
        cmp[k] = {"value": d.get(k), "baseline": round(b, 1) if b else None,
                  "delta": round(d[k] - b, 1) if _num(d.get(k)) and b else None}
    day_acts = [a for a in acts if a["date"] == target]
    notes = []
    s, sb = d.get("sleepSec"), cmp["sleepSec"]["baseline"]
    if _num(s) and sb:
        diff = (s - sb) / 60
        notes.append(_ins("good" if diff >= -20 else "warning", "Sleep", f"Slept {hm(s)} ({signed(diff, 0, ' min')} vs your 28-day average)" + (f", score {d['sleepScore']:.0f}." if _num(d.get("sleepScore")) else "."), "sleep"))
    h, hb = d.get("hrv"), cmp["hrv"]["baseline"]
    if _num(h) and hb:
        pct = (h / hb - 1) * 100
        notes.append(_ins("good" if pct > -7 else "warning" if pct > -15 else "serious", "HRV", f"{h:.0f} ms overnight ({signed(pct, 0, '%')} vs baseline).", "recovery"))
    r, rb = d.get("rhr"), cmp["rhr"]["baseline"]
    if _num(r) and rb:
        notes.append(_ins("good" if r - rb < 3 else "warning", "Resting HR", f"{r:.0f} bpm ({signed(r - rb, 1)} vs baseline).", "heart"))
    t = d.get("stress")
    if _num(t):
        notes.append(_ins("good" if t <= 35 else "warning", "Stress", f"Average stress {t:.0f}" + (f" ({signed(t - cmp['stress']['baseline'], 0)} vs usual)." if cmp["stress"]["baseline"] else "."), "mind"))
    if day_acts:
        tl = sum(a["load"] for a in day_acts)
        hard = any(is_hard(a) for a in day_acts)
        notes.append(_ins("info", "Training", f"{len(day_acts)} session(s), {hm(sum(a['dur'] for a in day_acts))}, load {tl:.0f}{' including a hard effort' if hard else ''}.", "training",
                          "Tomorrow should be easy or a rest day." if hard else None))
    elif _num(d.get("steps")):
        notes.append(_ins("info", "Rest day", f"No workout logged; {d['steps']:,.0f} steps.", "recovery"))
    nxt = days[idx + 1] if idx + 1 < len(days) else None
    if nxt and _num(nxt.get("hrv")) and hb:
        notes.append(_ins("info", "Next night", f"HRV the following night was {nxt['hrv']:.0f} ms ({signed((nxt['hrv'] / hb - 1) * 100, 0, '%')} vs baseline).", "pattern"))
    return {"date": target, "metrics": d, "compare": cmp, "activities": day_acts, "load": load[idx], "notes": notes}


# ----------------------------------------------------------------- entry point

def analyse(payload: dict, details: dict | None = None, weather: dict | None = None) -> dict:
    meta = payload["meta"]
    sets = payload["sets"]
    start, end = date.fromisoformat(meta["fetchStart"]), date.fromisoformat(meta["end"])
    days = build_days(sets, start, end)
    acts = build_activities(sets)
    load = daily_load(days, acts)
    hrp = hr_profile(sets, acts, baseline(days, "rhr", len(days), 28))
    flags = strain_flags(days)
    rd = readiness(days, load, flags)
    order = {"serious": 0, "critical": 0, "warning": 1, "info": 2, "good": 3}
    sort = lambda xs: sorted(xs, key=lambda x: order.get(x["level"], 9))  # noqa: E731
    return {
        "days": days,
        "activities": acts,
        "load": [round(x, 1) for x in load],
        "hrProfile": hrp,
        "flags": flags,
        "readiness": rd,
        "insights": {
            "health": sort(health_insights(days, acts, flags, sets)),
            "wellbeing": sort(wellbeing_insights(days, acts, load)),
            "training": sort(training_insights(days, acts, load)),
        },
        "running": running_report(days, acts, load, hrp, rd, flags, end, details, weather),
    }


def briefing(analysis: dict, today: str, source: str) -> dict:
    """The compact daily snapshot that gets stored."""
    run = analysis.get("running") or {}
    last = analysis["days"][-1] if analysis["days"] else {}
    prev = analysis["days"][-2] if len(analysis["days"]) > 1 else {}
    has_sleep = _num(last.get("sleepSec")) or _num(last.get("hrv"))
    return {
        "date": today,
        "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": source,
        "hasLastNight": bool(has_sleep),
        "readiness": analysis.get("readiness"),
        "today": run.get("today"),
        "nextWeek": run.get("nextWeek"),
        "flags": analysis.get("flags"),
        "insights": analysis.get("insights"),
        "runningInsights": run.get("insights"),
        "snapshot": {k: last.get(k) if _num(last.get(k)) else prev.get(k) for k in ("sleepSec", "sleepScore", "hrv", "rhr", "bbHigh", "stress")},
    }
