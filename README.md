# Health Insight

A private health and workout dashboard for your own Garmin data. It uses the
[Garmin Connect Personal API](https://developer.garmin.com/gc-developer-program/personal/personal-api)
and runs on your own machine or on Vercel. There's nothing to install beyond
Python 3.10+.


## Quick start

```bash
git clone https://github.com/mualshaadiq/health-insight.git
cd health-insight
cp .env.example .env        # then paste your key after GARMIN_PERSONAL_API_KEY=
python3 server.py           # open http://127.0.0.1:8787
```

Want to look before you connect anything? Run `python3 server.py --demo`. It
uses synthetic data and shows a **DEMO DATA** badge.

### Getting a key

Garmin Connect web → **Settings → Personal API**
([direct link](https://connect.garmin.com/app/settings/personalApiToken)) →
*Create Key*. Choose an expiry (30 days to 1 year). Garmin shows the key only
once.

The server also reads the key from the `GARMIN_PERSONAL_API_KEY` environment
variable, or from `~/.config/garmin/connect-personal-api-key`, which is the
same file Garmin's official Python client uses.

## Deploy to Vercel

The repo deploys as-is: `web/` is served as static files and `api/*.py` run
as Python serverless functions (see `vercel.json`). Set these environment
variables on the Vercel project:

| Variable | Purpose |
|---|---|
| `GARMIN_PERSONAL_API_KEY` | your Personal API key (mark it **Sensitive**) |
| `DASHBOARD_PASSWORD` | required on Vercel. The API refuses to serve data without it. Use a long random string. |
| `CRON_SECRET` | required for the morning refresh. Vercel sends it to `/api/cron`, and other callers are rejected. |
| `BLOB_READ_WRITE_TOKEN` | added automatically when you connect a **private** Vercel Blob store. Daily briefings are stored there. |
| `APP_TIMEZONE` | optional (e.g. `Asia/Jakarta`). Normally learned from your browser on first visit. |
| `GARMIN_CONNECT_TOKENS` | optional. Token JSON from `python3 garmin_sync.py token` to enable calendar sync (mark it **Sensitive**). |
| `EASY_HR_CAP` | optional. Fixes the easy heart-rate cap (e.g. `150`) instead of calculating it. |
| `WEATHER_LAT`, `WEATHER_LON` | optional, opt-in. Enables the Open-Meteo heat forecast for that location. |
| `DEMO_MODE` | optional. Set to `1` to serve synthetic data instead. |

### Daily refresh

A Vercel Cron job calls `/api/cron` every day at **22:00 UTC** (change
`crons` in `vercel.json` to suit your morning). It pulls fresh Garmin data,
recomputes everything, and saves that day's **briefing** to private Blob
storage: readiness, today's recommended run, next-week mileage and all
insights. Opening the dashboard also saves a briefing if none exists yet. If
the scheduled run happened before your watch synced last night's sleep, the
first visit after the sync regenerates it. **Refresh** always regenerates it.

The page asks for the password once. It's kept in the browser if you tick
"Remember", and **Lock** forgets it. All requests are marked `noindex`, and
data responses are `private, no-store`. The bearer token and a 15-minute
data cache live in the function's `/tmp`.

## What you get

### Overview
**Today:** a readiness estimate (0–100) that blends overnight HRV and resting
HR (each against your 28-day baseline), sleep score, Body Battery peak and
training-load ratio. It comes with tiles for sleep, HRV, resting HR, Body
Battery, steps and stress, plus **today's recommended run**.

**Health:**
- an early strain warning when resting HR, HRV and breathing rate move the wrong way together
- resting HR and HRV trends, the HRV balanced range and below-baseline streaks
- breathing rate, SpO₂, VO₂ max and fitness age, weight trend
- weekly intensity minutes vs WHO's 150–300 guideline

**Wellbeing:**
- sleep duration, sleep debt, deep/REM share
- bedtime regularity and weekend "social jet lag"
- stress (including workdays vs weekends) and Body Battery charge vs drain
- daily steps and rest-day frequency
- personal patterns: whether sleep length tracks your HRV, and whether evening workouts hurt your sleep

Every flag comes with a concrete **→ action**.

**Running coach:**
- a running fitness estimate (VDOT) from your best recent hard effort, or Garmin's VO₂ max as a fallback
- race predictions for 5K, 10K, half and full marathon
- Daniels-style training paces (easy, marathon, threshold, interval, repetition) plus your easy heart-rate ceiling
- **today's session:** rest, easy, long run, threshold or intervals, chosen from readiness, days since your last hard and long run, and load ratio
- **next 7 days:** a distance range (about +10%, held back when you're tired, or a deload after three building weeks) and a day-by-day plan that adds up to it
- running flags: mileage jumps, long-run share, easy runs done too hard, cadence, aerobic efficiency trend, running streaks

**Per-run detail:** each run's original activity file (FIT) is downloaded
once, analysed second by second, and saved. That covers:
- fastest 1K, 3K, 5K, 10K and half-marathon stretches *inside* any run
- per-km splits, and hill-adjusted pace (Minetti energy-cost model) so hilly runs aren't mistaken for slow ones
- heart-rate drift (aerobic decoupling) on steady runs, and even, negative or faded pacing
- interval detection from workout laps, with rep pace, consistency, fade and work/recovery HR
- time above your easy ceiling, from the full heart-rate trace rather than the average

**Base-building mode** switches on for runners with less than 10 weeks of
history, or whose recent runs spend 15%+ of moving time above the easy cap:
- **Easy cap** from heart-rate reserve: resting + 64.5% × (max − resting), about 150 bpm for resting 52 / max 204. Walk at cap + 5, jog again under cap − 10. Override with `EASY_HR_CAP`.
- **Run-walk ladder** 2:1 → 3:1 → 5:1 → continuous, picked from how much of your last 3 runs was above the walk trigger. Easy and long-run durations grow only as heart-rate control improves.
- Today's session and the weekly plan become capped run-walk sessions with a warm-up, strides on Thursday and a long run at the weekend. Faster work unlocks only when running stays under the cap.
- **Pace at your cap** for each run (walking included) is the progress test.
- **Heat:** the watch's temperature sensor flags hot conditions. Set `WEATHER_LAT` / `WEATHER_LON` to also get today's "feels like" forecast and coolest hour from Open-Meteo; this is off by default, so no location leaves the app unless you set it.

**Workouts on your watch:** the Personal API is read-only, so the dashboard
can't push to Garmin Connect. Instead, **Download workouts for your watch** (in
the base-phase card) gives you a zip of Garmin workout files (`.fit`): easy,
easy + strides, and long run at your current run-walk step, with the cap as the
heart-rate target. Copy them over USB into `GARMIN/NewFiles` on the watch; it
imports them into its workout list. Download a fresh set when you move up the
ladder. The files are built by `workouts.py` and `fit.encode_workout`, and
check out clean with Garmin's official FIT SDK decoder.

**Garmin Connect calendar sync (unofficial).** `garmin_sync.py` uses the
community library [`garminconnect`](https://pypi.org/project/garminconnect/)
to put the next 7 days of the base plan on your Garmin Connect calendar:
Tue easy, Thu easy + strides, Sat long, each with your heart-rate cap as the
target. The watch then picks them up on its next sync. This is not an official
Garmin API: it can break when Garmin changes things, and automated sign-ins may
be challenged.

```bash
pip install "garminconnect[workout]==0.3.20"
python3 garmin_sync.py login          # once, on your computer: email, password, MFA code
python3 garmin_sync.py push --dry-run # preview, then without --dry-run to send
python3 garmin_sync.py token          # print tokens to store as GARMIN_CONNECT_TOKENS (Sensitive) in Vercel
```

Your password is only typed into `login` and is never saved; the library keeps
refreshable tokens instead. On Vercel, **Send to Garmin Connect** (base-phase
card) and the morning job use `GARMIN_CONNECT_TOKENS`. Refreshed tokens are
saved to the private **Blob store**, which is needed so syncing keeps working
after a token refresh. The sync only touches workouts named `HI · …`. It is
idempotent, and when you move up the ladder it swaps the old sessions for new ones.

These results feed the coach:
- the fitness estimate uses your best segment efforts and even interval sessions
- runs are classified as easy, long, tempo, intervals or race
- interval sessions progress (one more rep when the last set was even; repeat it when the reps faded)
- the long run holds or grows based on last time's drift and pacing
- the recent-runs table and insights cover endurance, interval execution, long-run pacing and easy-run discipline

The overview shows a **Recent runs** table, and the daily page shows each
run's pace, HR and elevation charts, splits and best efforts.

Up to 15 new files are processed per visit and 40 by the morning job, so a
long history fills in over a few visits. Failed downloads are retried after a day.

**Training:** load ratio, consistency, intensity distribution, how hard days
affect your next-night HRV; weekly volume by sport, load chart, HR-zone time,
sport mix, and a table of recent activities (click a row to open that day).

### Daily page (`#/day/YYYY-MM-DD`)
For any date in the last ~11 months:
- that morning's **saved briefing**, showing what the dashboard recommended
- the day's metrics against your 28-day baseline, with "what stood out" notes
- a **sleep-stage chart**, heart rate through the day (with your easy ceiling), Body Battery through the day, and 5-minute overnight HRV readings
- that day's workouts

**Data sources** (overview) shows each Garmin endpoint's status, and you can
load its raw JSON. Choose 30 days, 90 days, 180 days or 1 year. Responses are
cached for 15 minutes.

## How it works

```
browser ──► server.py or api/*.py ──► personal.connectapi.garmin.com
              │  POST /personalapitoken-service/... (Authorization: ApiKey <key>)  → JWT, ≤24 h
              │  GET  sleep / hrv / stress / steps / body battery / activities … (Bearer JWT)
              ├─ analysis.py  → daily metrics, readiness, insights, running coach
              ├─ fit.py + run_detail.py → per-run analysis from each activity file
              └─ storage.py   → daily briefings + per-run results (private Vercel Blob, or .cache/store locally)
```

- `garmin_client.py` handles the key → bearer-token exchange (cached, renewed
  when less than 1 h of life remains, retried once on a 401). It also splits
  long ranges into 28-day chunks. The endpoint paths match Garmin's
  [official client](https://github.com/garmin/connect-personal-api-python).
- `core.py` holds the request logic shared by `server.py` (local) and the
  Vercel functions. Each data set is fetched on its own, so one failing
  endpoint doesn't block the rest. At least 120 days are always fetched, so
  baselines and running history work in the 30-day view too.
- `fit.py` decodes Garmin FIT activity files (pure Python), and `run_detail.py`
  turns the second-by-second samples into best efforts, splits, hill-adjusted
  pace, drift and interval structure.
- `analysis.py` is all the maths: tolerant field lookup, baselines,
  readiness, insights, and the running coach (Daniels/Gilbert VO₂ equations
  for VDOT, paces and race predictions).
- `web/` holds the dashboard (vanilla JS plus a bundled copy of Chart.js 4.4.1,
  MIT licensed). Field lookup tolerates different response shapes.

Other options:

```bash
python3 server.py --export snapshot.json --days 180   # dump raw data to a file
python3 server.py --port 9000
python3 -m unittest discover -s tests                 # offline tests (fake Garmin + analysis)
```

## Security

- `.env`, `.cache/` and `snapshot*.json` are git-ignored. **Never commit your key.**
- The local server binds to `127.0.0.1` by default. Your key is sent only to Garmin's token endpoint.
- On Vercel, data is only served with `DASHBOARD_PASSWORD`. Without it, the deployment fails closed.
- If a key leaks (for example, pasted into a chat or a commit), delete it in
  Garmin Connect and create a new one. Deleting a key also revokes every token
  issued from it.

The readiness score and insights are heuristics for your own use. They are
not medical advice.
