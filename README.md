# Health Insight

A private health and workout dashboard for your own Garmin data. It uses the
[Garmin Connect Personal API](https://developer.garmin.com/gc-developer-program/personal/personal-api)
and runs entirely on your machine. There's nothing to install beyond Python 3.10+.


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

The repo deploys as-is: `web/` is served as static files and `api/data.py`
runs as a Python serverless function (see `vercel.json`). Set these
environment variables on the Vercel project:

| Variable | Purpose |
|---|---|
| `GARMIN_PERSONAL_API_KEY` | your Personal API key (mark it **Sensitive**) |
| `DASHBOARD_PASSWORD` | required on Vercel. The API refuses to serve data without it. Use a long random string. |
| `DEMO_MODE` | optional. Set to `1` to serve synthetic data instead. |

The page asks for the password once. It's kept in the browser if you tick
"Remember", and **Lock** forgets it. All requests are marked `noindex`, and
data responses are `private, no-store`. The bearer token and a 15-minute
data cache live in the function's `/tmp`.

## What you get

**Today:** a readiness estimate (0–100) that blends overnight HRV and resting
HR (each against your 28-day baseline), sleep score, Body Battery peak and
training-load ratio. Next to it are tiles for sleep, HRV, resting HR, Body
Battery, steps and stress.

**Insights:** rule-based reads of your own trends, sorted with the most
important first:
- sleep duration, sleep debt and night-to-night consistency; deep/REM share
- HRV and resting HR this week vs your previous 4 weeks, and vs Garmin's balanced range
- acute:chronic training-load ratio (spike, productive or detraining)
- training consistency and volume change, plus the intensity split (time in Z1–2 vs Z4–5)
- personal patterns: how your HRV responds the night after your hardest days, and whether sleep length tracks your HRV
- steps vs goal, all-day stress, VO₂ max change

**Sleep & recovery charts:** sleep by stage, HRV with its balanced band,
resting HR, Body Battery high/low, stress, steps vs goal, VO₂ max and weight.

**Workouts:** weekly volume by sport, acute vs chronic training load,
time in HR zones, sport mix, and a table of recent activities.

**Data sources:** shows the status of each Garmin endpoint and its raw JSON.
Use it when a chart looks empty.

Choose 30 days, 90 days, 180 days or 1 year. Responses are cached for
15 minutes in `.cache/`. **Refresh** forces a fresh pull.

## How it works

```
browser ──► server.py (127.0.0.1 only) ──► personal.connectapi.garmin.com
              │  POST /personalapitoken-service/... (Authorization: ApiKey <key>)  → JWT, ≤24 h
              │  GET  sleep / hrv / stress / steps / body battery / activities … (Bearer JWT)
              └─ .cache/bearer-token.json (0600), .cache/data-*.json
```

- `garmin_client.py` handles the key → bearer-token exchange (cached, renewed
  when less than 1 h of life remains, retried once on a 401). It also splits
  long ranges into 28-day chunks. The endpoint paths match Garmin's
  [official client](https://github.com/garmin/connect-personal-api-python).
- `server.py` is a stdlib HTTP server. Each data set is fetched on its own,
  so one failing endpoint doesn't block the rest.
- `web/` holds the dashboard (vanilla JS plus a bundled copy of Chart.js 4.4.1,
  MIT licensed). Field lookup tolerates different response shapes.

Other options:

```bash
python3 server.py --export snapshot.json --days 180   # dump raw data to a file
python3 server.py --port 9000
python3 -m unittest discover -s tests                 # offline tests against a fake Garmin
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
