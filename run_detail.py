"""Per-run analysis from the second-by-second samples in a Garmin activity (FIT) file.

Produces a compact summary per run: best efforts, per-km splits, hill-adjusted
(grade-adjusted) pace, heart-rate drift (aerobic decoupling), pacing and
interval structure, plus a heart-rate histogram and a downsampled series for
charts. Everything is derived from the athlete's own data.
"""

from __future__ import annotations

import math
import statistics

VERSION = 3  # bump to re-analyse every saved run
BEST_DISTANCES = {"1k": 1000, "3k": 3000, "5k": 5000, "10k": 10000, "21.1k": 21097.5}
MOVING_MS = 0.8  # below this a runner is treated as stopped


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def _cv(xs):
    xs = [x for x in xs if x]
    return statistics.pstdev(xs) / statistics.mean(xs) * 100 if len(xs) > 1 else None


def minetti_factor(grade: float) -> float:
    """Energy cost of running on a grade relative to flat (Minetti 2002), softened downhill."""
    g = max(-0.25, min(0.25, grade))
    c = 155.4 * g**5 - 30.4 * g**4 - 43.3 * g**3 + 46.3 * g**2 + 19.5 * g + 3.6
    return max(0.85, c / 3.6)


def _samples(fit: dict):
    recs = [r for r in fit.get("records", []) if r.get("timestamp") is not None]
    recs.sort(key=lambda r: r["timestamp"])
    if len(recs) < 30:
        return None
    t0 = recs[0]["timestamp"]
    t, d, v, hr, cad, alt = [], [], [], [], [], []
    dist = 0.0
    prev_t = None
    for r in recs:
        ts = r["timestamp"] - t0
        if prev_t is not None and ts <= prev_t:
            continue
        sp = r.get("enhanced_speed", r.get("speed"))
        if r.get("distance") is not None:
            dist = max(dist, r["distance"])
        elif sp is not None and prev_t is not None:
            dist += sp * (ts - prev_t)
        if sp is None and t and ts > t[-1]:
            sp = (dist - d[-1]) / (ts - t[-1])
        c = r.get("cadence")
        if c is not None:
            c = (c + (r.get("fractional_cadence") or 0)) * 2  # FIT running cadence is per leg
        t.append(ts)
        d.append(dist)
        v.append(sp or 0.0)
        hr.append(r.get("heart_rate"))
        cad.append(c)
        alt.append(r.get("enhanced_altitude", r.get("altitude")))
        prev_t = ts
    return {"t": t, "d": d, "v": v, "hr": hr, "cad": cad, "alt": alt}


def _smooth(xs, k=5):
    out = []
    for i in range(len(xs)):
        win = [x for x in xs[max(0, i - k):i + k + 1] if x is not None]
        out.append(sum(win) / len(win) if win else None)
    return out


def _time_at(t, d, target):
    """Interpolated elapsed time when cumulative distance first reaches target."""
    lo, hi = 0, len(d) - 1
    if d[hi] < target:
        return None
    while lo < hi:
        mid = (lo + hi) // 2
        if d[mid] < target:
            lo = mid + 1
        else:
            hi = mid
    if lo == 0 or d[lo] == d[lo - 1]:
        return t[lo]
    f = (target - d[lo - 1]) / (d[lo] - d[lo - 1])
    return t[lo - 1] + f * (t[lo] - t[lo - 1])


def _best_effort(t, d, hr, dist):
    if d[-1] < dist:
        return None
    best, j = None, 0
    for i in range(len(d)):
        while j < len(d) and d[j] - d[i] < dist:
            j += 1
        if j >= len(d):
            break
        span = d[j] - d[i]
        sec = (t[j] - t[i]) * dist / span if span else None
        if sec and (best is None or sec < best[0]):
            best = (sec, i, j)
    if not best:
        return None
    sec, i, j = best
    return {"sec": round(sec, 1), "hr": round(_mean(hr[i:j + 1])) if _mean(hr[i:j + 1]) else None, "startKm": round(d[i] / 1000, 2)}


def analyse_fit(fit: dict, activity_id=None) -> dict | None:
    s = _samples(fit)
    if not s:
        return None
    t, d, v, hr, cad, alt = s["t"], s["d"], s["v"], s["hr"], s["cad"], s["alt"]
    n = len(t)
    dt = [0] + [min(t[i] - t[i - 1], 10) for i in range(1, n)]  # cap gaps (auto-pause)
    moving = [v[i] >= MOVING_MS for i in range(n)]
    moving_sec = sum(dt[i] for i in range(n) if moving[i])
    dist = d[-1]
    if dist < 500 or moving_sec < 300:
        return None

    # hill-adjusted distance: grade over ~30 m windows on smoothed altitude
    alt_s = _smooth(alt, 5) if any(a is not None for a in alt) else [None] * n
    adj = [0.0] * n
    gain = 0.0
    j = 0
    for i in range(1, n):
        dd = d[i] - d[i - 1]
        f = 1.0
        if alt_s[i] is not None:
            while j < i and d[i] - d[j] > 30:
                j += 1
            k = max(0, j - 1)
            if alt_s[k] is not None and d[i] - d[k] >= 15:
                f = minetti_factor((alt_s[i] - alt_s[k]) / (d[i] - d[k]))
            if alt_s[i - 1] is not None and alt_s[i] > alt_s[i - 1]:
                gain += alt_s[i] - alt_s[i - 1]
        adj[i] = dd * f
    adj_cum = [0.0] * n
    adj_cum[0] = d[0]  # distance already covered at the first sample
    for i in range(1, n):
        adj_cum[i] = adj_cum[i - 1] + adj[i]
    gap_speed = adj_cum[-1] / moving_sec if moving_sec else None
    raw_speed = dist / moving_sec if moving_sec else None

    # per-km splits
    splits = []
    prev_t, prev_i = 0.0, 0
    for km in range(1, int(dist // 1000) + 1):
        tk = _time_at(t, d, km * 1000)
        if tk is None:
            break
        i_end = next((i for i in range(prev_i, n) if t[i] >= tk), n - 1)
        seg_hr = _mean(hr[prev_i:i_end + 1])
        seg_adj = adj_cum[i_end] - adj_cum[prev_i]
        seg_alt = [a for a in alt_s[prev_i:i_end + 1] if a is not None]
        sec = tk - prev_t
        splits.append({"km": km, "sec": round(sec), "hr": round(seg_hr) if seg_hr else None,
                       "gapSec": round(sec * 1000 / seg_adj) if seg_adj > 0 else None,
                       "elev": round(seg_alt[-1] - seg_alt[0]) if len(seg_alt) > 1 else None})
        prev_t, prev_i = tk, i_end
    rem = dist - len(splits) * 1000
    if rem >= 200:
        sec = t[-1] - prev_t
        seg_adj = adj_cum[-1] - adj_cum[prev_i]
        splits.append({"km": round(len(splits) + rem / 1000, 2), "sec": round(sec), "pace": round(sec / rem * 1000),
                       "hr": round(_mean(hr[prev_i:])) if _mean(hr[prev_i:]) else None,
                       "gapSec": round(sec * 1000 / seg_adj) if seg_adj > 0 else None, "elev": None, "partial": True})

    full = [sp for sp in splits if not sp.get("partial")]
    pace_cv = _cv([sp["gapSec"] or sp["sec"] for sp in full]) if len(full) >= 3 else None

    # pacing: hill-adjusted halves by distance
    half_t = _time_at(t, d, dist / 2)
    halves = None
    if half_t and dist >= 3000:
        i_half = next(i for i in range(n) if t[i] >= half_t)
        a1, a2 = adj_cum[i_half], adj_cum[-1] - adj_cum[i_half]
        s1, s2 = half_t, t[-1] - half_t
        if a1 > 0 and a2 > 0:
            p1, p2 = s1 / a1 * 1000, s2 / a2 * 1000  # sec per adjusted km
            halves = {"first": round(p1), "second": round(p2), "pct": round((p2 / p1 - 1) * 100, 1)}
    fade = None
    if len(full) >= 5:
        last = _mean([sp["gapSec"] or sp["sec"] for sp in full[-max(1, len(full) // 10):]])
        avg = _mean([sp["gapSec"] or sp["sec"] for sp in full])
        fade = round((last / avg - 1) * 100, 1) if avg else None

    # intervals from laps
    intervals = _intervals(fit.get("laps") or [])
    steady = intervals is None and pace_cv is not None and pace_cv < 10

    # aerobic decoupling over the moving portion, skipping the first 10 minutes
    decoupling = None
    if steady and moving_sec >= 30 * 60 and any(h for h in hr):
        idx = [i for i in range(n) if moving[i] and hr[i] and t[i] >= 600]
        if len(idx) > 120:
            mid = len(idx) // 2

            def ef(ids):
                spd = (adj_cum[ids[-1]] - adj_cum[ids[0]]) / max(1, t[ids[-1]] - t[ids[0]])
                return spd / _mean([hr[i] for i in ids])

            e1, e2 = ef(idx[:mid]), ef(idx[mid:])
            decoupling = round((e1 - e2) / e1 * 100, 1) if e1 else None

    # heart-rate histogram (5 bpm bins, seconds) and best efforts
    hist = {}
    for i in range(n):
        if hr[i] and moving[i]:
            b = int(hr[i] // 5 * 5)
            hist[b] = hist.get(b, 0) + dt[i]
    best = {k: be for k, m in BEST_DISTANCES.items() if (be := _best_effort(t, d, hr, m))}

    # ~200-point series for charts: [km, pace s/km (smoothed), hr, altitude]
    step = max(1, n // 200)
    vs = _smooth(v, 10)
    series = []
    for i in range(0, n, step):
        pace = round(1000 / vs[i]) if vs[i] and vs[i] > MOVING_MS else None
        series.append([round(d[i] / 1000, 3), pace if pace and pace < 1200 else None, hr[i], round(alt_s[i], 1) if alt_s[i] is not None else None])

    hr_moving = [hr[i] for i in range(n) if moving[i] and hr[i]]
    cad_moving = [cad[i] for i in range(n) if moving[i] and cad[i]]
    return {
        "v": VERSION, "id": activity_id,
        "distM": round(dist), "movingSec": round(moving_sec), "elapsedSec": round(t[-1]),
        "paceSec": round(1000 / raw_speed) if raw_speed else None,
        "gapSec": round(1000 / gap_speed) if gap_speed else None,
        "avgHR": round(_mean(hr_moving)) if hr_moving else None, "maxHR": max(hr_moving) if hr_moving else None,
        "cadence": round(_mean(cad_moving)) if cad_moving else None, "elevGain": round(gain),
        "best": best, "splits": splits[:60], "paceCv": round(pace_cv, 1) if pace_cv is not None else None,
        "halves": halves, "fadePct": fade, "steady": steady, "decoupling": decoupling,
        "intervals": intervals, "hrHist": {str(k): round(s) for k, s in sorted(hist.items())},
        "series": series,
    }


def _intervals(laps: list[dict]):
    """Detect a work/recovery structure from laps (Garmin workout steps or manual laps)."""
    rows = []
    for lap in laps:
        sec = lap.get("total_timer_time") or lap.get("total_elapsed_time")
        dist = lap.get("total_distance")
        if not sec or not dist:
            continue
        rows.append({"sec": sec, "dist": dist, "speed": dist / sec, "hr": lap.get("avg_heart_rate"), "intensity": lap.get("intensity")})
    if len(rows) < 4:
        return None
    work_kinds, rest_kinds = {0, 5}, {1, 4}
    has_structure = any(r["intensity"] in rest_kinds for r in rows) and any(r["intensity"] in work_kinds for r in rows)
    if has_structure:
        work = [r for r in rows if r["intensity"] in work_kinds]
        rest = [r for r in rows if r["intensity"] in rest_kinds]
    else:
        med = statistics.median(r["speed"] for r in rows)
        work = [r for r in rows if r["speed"] >= med * 1.12 and r["sec"] >= 45]
        rest = [r for r in rows if r["speed"] < med * 0.95]
        # need alternation: auto 1 km laps on a steady run won't qualify
        if len(work) < 3 or len(rest) < 2:
            return None
    work = [r for r in work if r["sec"] >= 30]
    if len(work) < 3:
        return None
    paces = [1000 / r["speed"] for r in work]
    return {
        "reps": len(work),
        "repSec": round(statistics.median(r["sec"] for r in work)),
        "repDist": round(statistics.median(r["dist"] for r in work)),
        "paceSec": round(sum(r["sec"] for r in work) / sum(r["dist"] for r in work) * 1000),
        "cvPct": round(_cv(paces), 1),
        "fadePct": round((paces[-1] / paces[0] - 1) * 100, 1),
        "hrWork": round(_mean([r["hr"] for r in work])) if _mean([r["hr"] for r in work]) else None,
        "hrRest": round(_mean([r["hr"] for r in rest])) if rest and _mean([r["hr"] for r in rest]) else None,
    }


def without_series(detail: dict) -> dict:
    return {k: v for k, v in detail.items() if k not in ("series", "hrHist")}


def time_above(detail: dict, bpm: float) -> float | None:
    """Share (0-1) of moving time with heart rate at or above bpm."""
    hist = detail.get("hrHist") or {}
    tot = sum(hist.values())
    if not tot:
        return None
    return sum(s for b, s in hist.items() if int(b) + 2.5 >= bpm) / tot


def pace_fmt(sec: float | None) -> str:
    if not sec or not math.isfinite(sec):
        return "–"
    s = round(sec)
    return f"{s // 60}:{s % 60:02d}/km"
