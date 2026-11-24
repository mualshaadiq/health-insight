"""Put the base-building plan on your Garmin Connect calendar (it then syncs to the watch).

Uses the community library `garminconnect` (unofficial: it talks to Garmin Connect the way
Garmin's own app does). It is not endorsed by Garmin and can break when Garmin changes things.

Your Garmin password is only ever typed into `login` on your own computer. After that the
library works from saved tokens, which refresh themselves.

    python3 garmin_sync.py login            # once: sign in (+ MFA), saves ~/.garminconnect
    python3 garmin_sync.py token            # print the token JSON to store as GARMIN_CONNECT_TOKENS in Vercel
    python3 garmin_sync.py push [--dry-run] # put the next 7 days of the plan on your calendar

Workouts this tool creates are named "HI · ..." so it only ever touches its own items.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

import workouts

PREFIX = "HI · "
# weekday -> (kind, strides) for the base phase: Tue easy, Thu easy + strides, Sat long
LAYOUT = {1: ("easy", False), 3: ("easy", True), 5: ("long", False)}
TOKEN_DIR = Path(os.environ.get("GARMINTOKENS", "~/.garminconnect")).expanduser()


# ------------------------------------------------------------------ workout building

def workout_name(kind: str, base: dict, strides: bool) -> str:
    ratio = "run" if not base.get("jog") else f"{base['jog']}:{base['walk']}"
    minutes = workouts._minutes(base["longMin"] if kind == "long" else base["easyMin"])
    label = "Long" if kind == "long" else ("Easy+strides" if strides else "Easy")
    return f"{PREFIX}{label} {ratio} {minutes}m ≤{base['cap']}"


def build_workout(kind: str, base: dict, strides: bool):
    """A garminconnect RunningWorkout built from the same steps as the .fit files."""
    from garminconnect import workout as W

    minutes = workouts._minutes(base["longMin"] if kind == "long" else base["easyMin"])
    steps = workouts._session("", minutes, base, strides)
    order = iter(range(1, 1000))

    def make(s):
        if s.get("hr"):
            target = W.CustomHeartRateTarget(lower_limit=s["hr"][0], upper_limit=s["hr"][1])
            if s["intensity"] == workouts.fit.INT_WARMUP:
                st = W.create_warmup_step(s["sec"], next(order))
                kw = W._target_kwargs(target, None)
                return st.model_copy(update={"targetType": kw["target_type"], "targetValueOne": kw["target_value_one"],
                                             "targetValueTwo": kw["target_value_two"], "description": s.get("label")})
            st = W.create_targeted_interval_step(s["sec"], next(order), target)
        elif s["intensity"] == workouts.fit.INT_WARMUP:
            st = W.create_warmup_step(s["sec"], next(order))
        elif s["intensity"] == workouts.fit.INT_COOLDOWN:
            st = W.create_cooldown_step(s["sec"], next(order))
        elif s["intensity"] == workouts.fit.INT_RECOVERY:
            st = W.create_recovery_step(s["sec"], next(order))
        else:
            st = W.create_interval_step(s["sec"], next(order))
        return st.model_copy(update={"description": s.get("label")})

    out, i = [], 0
    while i < len(steps):
        # a repeat closes the run of steps that starts at its from_step
        rep = next((j for j in range(i, len(steps)) if steps[j]["kind"] == "repeat" and steps[j]["from_step"] == i), None)
        if rep is not None:
            group_order = next(order)
            children = [make(s) for s in steps[i:rep]]
            out.append(W.create_repeat_group(steps[rep]["times"], children, group_order))
            i = rep + 1
        else:
            out.append(make(steps[i]))
            i += 1

    total = 0
    for i, s in enumerate(steps):
        if s["kind"] == "time":
            reps = next((r["times"] for r in steps[i:] if r["kind"] == "repeat" and r["from_step"] <= i), 1)
            total += s["sec"] * reps
    rules = (f"Keep HR ≤ {base['cap']}. At {base['walkAbove']} walk until under {base['resumeBelow']}. "
             "Short quick steps, no fast finish. From Health Insight.")
    return W.RunningWorkout(
        workoutName=workout_name(kind, base, strides),
        estimatedDurationInSecs=int(total),
        description=rules,
        workoutSegments=[W.WorkoutSegment(segmentOrder=1, sportType={"sportTypeId": W.SportType.RUNNING, "sportTypeKey": "running"}, workoutSteps=out)],
    )


def desired_schedule(base: dict, today: date, days: int = 7, ran_today: bool = False) -> list[tuple[str, str, bool]]:
    """[(date, kind, strides)] for the next `days` days following the base-phase week layout."""
    out = []
    for k in range(days):
        d = today + timedelta(days=k)
        if d.weekday() in LAYOUT and not (k == 0 and ran_today):
            kind, strides = LAYOUT[d.weekday()]
            out.append((d.isoformat(), kind, strides))
    return out


# ------------------------------------------------------------------ talking to Garmin Connect

def _pick(d: dict, *keys):
    for k in keys:
        if isinstance(d, dict) and d.get(k) not in (None, ""):
            return d[k]
    return None


def sync(garmin, base: dict, today: date, ran_today: bool = False, dry_run: bool = False) -> dict:
    """Make the next 7 days of the calendar match the plan. Idempotent; touches only "HI · " items."""
    want = desired_schedule(base, today, ran_today=ran_today)
    end = (today + timedelta(days=7)).isoformat()
    report = {"scheduled": [], "kept": [], "removed": [], "deletedTemplates": [], "dryRun": dry_run}

    # our workout templates, by name
    library = {}
    for w in garmin.get_workouts(0, 200) or []:
        name = _pick(w, "workoutName")
        if name and name.startswith(PREFIX):
            library[name] = _pick(w, "workoutId")

    # what's already on the calendar in the window
    on_cal = []
    for item in garmin.get_scheduled_workouts_by_period(today.isoformat(), end) or []:
        wk = item.get("workout") if isinstance(item.get("workout"), dict) else {}
        name = _pick(item, "workoutName", "title") or _pick(wk, "workoutName")
        if not name or not name.startswith(PREFIX):
            continue
        on_cal.append({"date": str(_pick(item, "date", "calendarDate", "scheduleDate"))[:10], "name": name,
                       "sid": _pick(item, "workoutScheduleId", "scheduledWorkoutId", "id")})

    wanted = {(d, workout_name(kind, base, strides)): (kind, strides) for d, kind, strides in want}
    for c in on_cal:
        if (c["date"], c["name"]) in wanted:
            report["kept"].append(f"{c['date']} {c['name']}")
            wanted.pop((c["date"], c["name"]))
        else:
            report["removed"].append(f"{c['date']} {c['name']}")
            if not dry_run and c["sid"]:
                garmin.unschedule_workout(c["sid"])

    for (d, name), (kind, strides) in sorted(wanted.items()):
        if not dry_run:
            wid = library.get(name)
            if not wid:
                res = garmin.upload_running_workout(build_workout(kind, base, strides))
                wid = _pick(res, "workoutId")
                library[name] = wid
            garmin.schedule_workout(wid, d)
        report["scheduled"].append(f"{d} {name}")

    # tidy up templates from earlier ladder steps that nothing references any more
    in_use = {n for _, n in wanted} | {c["name"] for c in on_cal if f"{c['date']} {c['name']}" in report["kept"]}
    in_use |= {workout_name(k, base, s) for _, k, s in want}
    for name, wid in library.items():
        if name not in in_use and wid:
            report["deletedTemplates"].append(name)
            if not dry_run:
                try:
                    garmin.delete_workout(wid)
                except Exception:  # noqa: BLE001 - still scheduled in the past, keep it
                    report["deletedTemplates"].pop()
    if not dry_run and report["scheduled"]:
        try:
            garmin.send_scheduled_workouts_to_devices()
        except Exception:  # noqa: BLE001 - calendar sync still delivers them
            pass
    return report


def client_from_tokens(token_json: str, workdir: Path):
    """Log in from token JSON (no password). Returns (garmin, read_back) where read_back() gives refreshed tokens."""
    from garminconnect import Garmin

    workdir.mkdir(parents=True, exist_ok=True, mode=0o700)
    f = workdir / "garmin_tokens.json"
    fd = os.open(f, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(token_json)
    g = Garmin()
    g.login(str(workdir))

    def read_back() -> str:
        try:
            return g.client.dumps()
        except Exception:  # noqa: BLE001
            return f.read_text()
    return g, read_back


# ------------------------------------------------------------------ CLI (run on your own computer)

def _cli() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "help"
    if cmd == "login":
        import getpass

        from garminconnect import Garmin
        email = input("Garmin email: ").strip()
        pw = getpass.getpass("Garmin password (not shown, not stored): ")
        g = Garmin(email, pw, prompt_mfa=lambda: input("MFA code from email/app: ").strip())
        g.login(str(TOKEN_DIR))
        print(f"Signed in. Tokens saved in {TOKEN_DIR} (owner-only). Your password was not saved.")
    elif cmd == "token":
        f = TOKEN_DIR / "garmin_tokens.json"
        print(f.read_text().strip())
        print("\nStore this as GARMIN_CONNECT_TOKENS (type: Sensitive) in Vercel. Treat it like a password.", file=sys.stderr)
    elif cmd == "push":
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from garminconnect import Garmin

        import analysis
        import core
        from garmin_client import load_dotenv
        load_dotenv(Path(__file__).resolve().parent / ".env")
        tz = os.environ.get("APP_TIMEZONE")
        raw = core.get_raw(core.MIN_FETCH_DAYS, tz, True)
        acts = analysis.build_activities(raw["sets"])
        today = date.fromisoformat(raw["meta"]["end"])
        details, _ = core.ensure_details(acts, today, budget_s=120, max_new=60)
        base = (analysis.analyse(raw, details).get("running") or {}).get("base")
        if not base or not base.get("active"):
            sys.exit("No active base-building plan to sync.")
        g = Garmin()
        g.login(str(TOKEN_DIR))
        ran_today = any(a["date"] == today.isoformat() and a["family"] == "run" for a in acts)
        print(json.dumps(sync(g, base, today, ran_today, dry_run="--dry-run" in sys.argv), indent=2, ensure_ascii=False))
    else:
        print(__doc__)


if __name__ == "__main__":
    _cli()
