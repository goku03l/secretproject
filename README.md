# Eight Sleep insights dashboard

A Streamlit dashboard that reads an Eight Sleep Pod's data from Eight Sleep's cloud and shows
sleep trends, patterns and device status. It is **read-only**: apart from the login request,
every call it makes is a GET.

Eight Sleep does not publish this API. The endpoints here were found by probing; they can
change or stop working at any time, and using them may be against Eight Sleep's terms.

## What it shows

| Tab | Contents |
|---|---|
| Overview | Average score, sleep, deep, REM, heart rate, HRV and breathing, with last-7-nights change; computed insights; both bed sides compared |
| Trends | Sleep by stage, heart rate, HRV, breathing, bed and room temperature, time awake, toss and turn, snoring |
| Patterns | Score by weekday, sleep timing, which factors correlate with the score, best and worst nights |
| Night detail | Stage timeline, heart rate, HRV, breathing and temperature for one night |
| Temperature & schedule | Current state, temperature levels, Autopilot, bedtime schedules, alarms |
| Pod & household | Device status, household, priming history, sleep streak challenges |
| API explorer | Every endpoint in the catalog, which ones answer, and their raw responses |

The insights are computed by the dashboard from the nightly data. Correlations show what
tends to occur together, not what causes what.

## Setup

Requires Python 3.11+.

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows; use `source .venv/bin/activate` elsewhere
pip install -r requirements.txt

copy .env.example .env            # then fill in EIGHT_EMAIL and EIGHT_PASSWORD
```

The account must sign in with an email and password (not Google or Apple sign-in).

## Deploying on Streamlit Community Cloud

1. In the app's **Settings → Secrets**, add:

   ```toml
   EIGHT_EMAIL = "you@example.com"
   EIGHT_PASSWORD = "your-password"
   EIGHT_TZ = "Asia/Kolkata"
   ```

2. In **Settings → Sharing**, restrict who can view the app. A public app lets anyone with the
   link see your sleep and health data.

## Run

```bash
streamlit run streamlit_app.py
```

Then open http://localhost:8501.

## Probe the endpoints

```bash
python eight_api.py
```

This tries every endpoint in the catalog with a GET, prints which ones answer, saves the raw
responses under `out/`, and rewrites `endpoints_working.json`, which the dashboard uses to
decide what to show in the API explorer.

## Files

- `streamlit_app.py` — the dashboard.
- `eight_api.py` — small read-only API client and the endpoint catalog.
- `endpoints_working.json` — endpoints that answered on the last probe.

## Privacy

`.env` and `out/` hold your login and your sleep data. Both are git-ignored; keep them out of
version control. The dashboard reads the first Pod on the account only.
