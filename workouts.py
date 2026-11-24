"""Turn the base-building plan into Garmin workout files for the watch.

Copy the .fit files into the watch's GARMIN/NewFiles folder over USB; the watch
imports them into its workout list. Steps carry custom heart-rate targets so the
watch alerts when you drift above the cap.
"""

from __future__ import annotations

import io
import time
import zipfile

import fit


def _minutes(spec) -> int:
    """'35–40' -> 40, '45' -> 45, 50 -> 50."""
    if isinstance(spec, (int, float)):
        return int(spec)
    return int(str(spec).replace("–", "-").split("-")[-1])


def _session(name: str, total_min: int, base: dict, strides: bool = False) -> list[dict]:
    cap = base["cap"]
    steps = [
        {"kind": "time", "label": "Brisk walk", "sec": 300, "intensity": fit.INT_WARMUP},
        {"kind": "time", "label": "Very slow jog", "sec": 300, "intensity": fit.INT_WARMUP, "hr": (90, cap - 5)},
    ]
    main_min = max(10, total_min - 15)  # 10 min warm-up + 5 min cool-down
    if base.get("jog"):
        jog, walk = base["jog"], base["walk"]
        reps = max(3, round(main_min / (jog + walk)))
        first = len(steps)
        steps += [
            {"kind": "time", "label": f"Jog, HR under {cap}", "sec": jog * 60, "intensity": fit.INT_ACTIVE, "hr": (90, cap)},
            {"kind": "time", "label": "Walk", "sec": walk * 60, "intensity": fit.INT_RECOVERY},
            {"kind": "repeat", "from_step": first, "times": reps},
        ]
    else:
        steps.append({"kind": "time", "label": f"Easy run, HR under {cap}", "sec": main_min * 60, "intensity": fit.INT_ACTIVE, "hr": (90, cap)})
    if strides:
        first = len(steps)
        steps += [
            {"kind": "time", "label": "Relaxed stride", "sec": 20, "intensity": fit.INT_ACTIVE},
            {"kind": "time", "label": "Walk back", "sec": 60, "intensity": fit.INT_RECOVERY},
            {"kind": "repeat", "from_step": first, "times": 6},
        ]
    steps.append({"kind": "time", "label": "Walk", "sec": 300, "intensity": fit.INT_COOLDOWN})
    return steps


def plan_files(base: dict) -> dict[str, bytes]:
    """Workout files for the current step of the plan: easy, easy + strides, long."""
    now = int(time.time())
    ratio = "Run" if not base.get("jog") else f"{base['jog']}-{base['walk']}"
    easy, long_ = _minutes(base["easyMin"]), _minutes(base["longMin"])
    out = {}
    for fname, title, minutes, strides in (
        (f"HI_Easy_{ratio}_{easy}min.fit", f"Easy {ratio} {easy}m HR<{base['cap']}", easy, False),
        (f"HI_Easy_Strides_{ratio}_{easy}min.fit", f"Easy+strides {ratio} {easy}m", easy, True),
        (f"HI_Long_{ratio}_{long_}min.fit", f"Long {ratio} {long_}m HR<{base['cap']}", long_, False),
    ):
        out[fname] = fit.encode_workout(title, _session(title, minutes, base, strides), now)
    return out


README = """Health Insight workouts for your watch
======================================

1. Connect the watch to a computer with its USB cable.
   - Windows: it appears in File Explorer as "Forerunner 165".
   - Mac: install OpenMTP (free) to browse the watch.
2. Open Internal Storage > GARMIN > NewFiles.
3. Copy the .fit files into NewFiles, then eject and unplug the watch.
4. On the watch: hold UP (menu) > Training > Workouts (or start Run, then
   hold UP > Training > Workouts). The workouts appear in the list.
   If they don't, restart the watch once.

The run steps have a heart-rate target, so the watch alerts you when you go
above your cap. When the dashboard moves you up the ladder (2:1 -> 3:1 ->
5:1 -> continuous), download the new set, and delete the old ones on the watch
or in Garmin Connect.
"""


def plan_zip(base: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in plan_files(base).items():
            z.writestr(name, data)
        z.writestr("HOW_TO_INSTALL.txt", README)
    return buf.getvalue()
