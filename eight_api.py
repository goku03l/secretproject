"""Small synchronous, READ-ONLY client for Eight Sleep's cloud API, plus an endpoint catalog.

Only GET requests are ever sent (the one POST is the login token request).
Login details come from environment variables (or a local .env file). No other
project files or libraries are needed besides httpx.

    python eight_api.py            # probe every catalog endpoint, print what answers
"""

from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent

# Login endpoint and the Eight Sleep app's client id/secret, as published in the
# open-source pyEight library (github.com/lukas-clarke/pyEight). They identify the
# app, not your account. Override with EIGHT_CLIENT_ID / EIGHT_CLIENT_SECRET if they change.
AUTH_URL = "https://auth-api.8slp.net/v1/tokens"
KNOWN_CLIENT_ID = "0894c7f33bb94800a03f1f4df13a4f38"
KNOWN_CLIENT_SECRET = "f0954a3ed5763ba3d06834c73731a32f15f168f47d4f164751275def86db0c76"

CLIENT = "https://client-api.8slp.net/v1"
APP = "https://app-api.8slp.net"
HEADERS = {
    "content-type": "application/json",
    "accept": "application/json",
    "accept-encoding": "gzip",
    "user-agent": "okhttp/4.9.3",
}


@dataclass
class Endpoint:
    key: str
    title: str
    url: str                      # may contain {uid} and {did}
    scope: str                    # "account" | "device" | "user"
    about: str
    params: dict = field(default_factory=dict)   # values may contain {from}, {to}, {tz}
    verified: bool = True         # False = a guess that is only kept if the probe gets a 200


CATALOG: list[Endpoint] = [
    # ---- confirmed in this project -------------------------------------------------
    Endpoint("me", "Account", CLIENT + "/users/me", "account", "Logged-in user's profile, features and device list."),
    Endpoint("device", "Pod device record", CLIENT + "/devices/{did}", "device", "Live Pod state: heating levels, water, WiFi, firmware, cover, schedules."),
    Endpoint("profile", "User profile", CLIENT + "/users/{uid}", "user", "Per-side user profile and preferences."),
    Endpoint("current_device", "Bed side", CLIENT + "/users/{uid}/current-device", "user", "Which device and side the user sleeps on."),
    Endpoint("trends", "Sleep trends", CLIENT + "/users/{uid}/trends", "user", "Nightly scores, durations, stages and vitals time series.",
             {"tz": "{tz}", "from": "{from}", "to": "{to}", "include-main": "false", "include-all-sessions": "true", "model-version": "v2"}),
    Endpoint("intervals", "Sleep intervals", CLIENT + "/users/{uid}/intervals", "user", "The most recent sleep sessions with stages and time series."),
    Endpoint("temperature", "Temperature state", APP + "/v1/users/{uid}/temperature", "user", "Current level, smart schedule levels and next bedtime schedule."),
    Endpoint("alarms", "Alarms", APP + "/v1/users/{uid}/alarms", "user", "Wake alarms with vibration, thermal and smart settings."),
    Endpoint("routines", "Routines", APP + "/v2/users/{uid}/routines", "user", "Routines and one-off alarms, with current alarm state."),
    Endpoint("bedtime", "Bedtime settings", APP + "/v1/users/{uid}/bedtime", "user", "Bedtime schedule settings."),
    Endpoint("base", "Adjustable base", APP + "/v1/users/{uid}/base", "user", "Base angles and presets (only with a Pod base)."),
    # ---- candidates: kept only if they answer --------------------------------------
    Endpoint("routines_v1", "Routines (v1)", APP + "/v1/users/{uid}/routines", "user", "Older routines format.", verified=False),
    Endpoint("autopilot", "Autopilot details", APP + "/v1/users/{uid}/autopilotDetails", "user", "Autopilot settings and status.", verified=False),
    Endpoint("autopilot_recap", "Autopilot recap", APP + "/v1/users/{uid}/autopilotDetails/autopilotRecap", "user", "What Autopilot changed last night.",
             {"day": "{to}", "tz": "{tz}"}, verified=False),
    Endpoint("level_suggestions", "Level suggestions", APP + "/v1/users/{uid}/level-suggestions", "user", "Suggested temperature levels.", verified=False),
    Endpoint("away_mode", "Away mode", APP + "/v1/users/{uid}/away-mode", "user", "Away mode status.", verified=False),
    Endpoint("nap_mode", "Nap mode", APP + "/v1/users/{uid}/temperature/nap-mode/status", "user", "Nap mode status.", verified=False),
    Endpoint("insights", "Insights", APP + "/v1/users/{uid}/insights", "user", "Sleep insights.", verified=False),
    Endpoint("insights_v2", "Insights (v2)", APP + "/v2/users/{uid}/insights", "user", "Sleep insights.", {"date": "{to}"}, verified=False),
    Endpoint("tailored", "Tailored insights", APP + "/v1/users/{uid}/tailored-insights", "user", "Personalised insights.", verified=False),
    Endpoint("challenges", "Challenges", APP + "/v1/users/{uid}/challenges", "user", "Sleep challenges.", verified=False),
    Endpoint("metrics_summary", "Metrics summary", APP + "/v1/users/{uid}/metrics/summary", "user", "Aggregated metrics.", verified=False),
    Endpoint("metrics_aggregate", "Metrics aggregate", APP + "/v1/users/{uid}/metrics/aggregate", "user", "Aggregated metrics.",
             {"from": "{from}", "to": "{to}", "tz": "{tz}"}, verified=False),
    Endpoint("health_check", "Health check", APP + "/v1/users/{uid}/health-check", "user", "Health check results.", verified=False),
    Endpoint("release_features", "Release features", APP + "/v1/users/{uid}/release-features", "user", "Feature flags for the user.", verified=False),
    Endpoint("release_features_v2", "Release features (v2)", APP + "/v2/users/{uid}/release-features", "user", "Feature flags for the user.", verified=False),
    Endpoint("user_devices", "User devices", APP + "/v1/users/{uid}/devices", "user", "Devices linked to the user.", verified=False),
    Endpoint("audio_tracks", "Alarm audio tracks", APP + "/v1/users/{uid}/audio/tracks", "user", "Available alarm sounds.", verified=False),
    Endpoint("audio_player", "Audio player", APP + "/v1/users/{uid}/audio/player", "user", "Audio player state.", verified=False),
    Endpoint("household_summary", "Household summary", APP + "/v1/household/users/{uid}/summary", "user", "Household members and devices.", verified=False),
    Endpoint("household_schedule", "Household schedule", APP + "/v1/household/users/{uid}/schedule", "user", "Household schedule.", verified=False),
    Endpoint("household_current", "Household current set", APP + "/v1/household/users/{uid}/current-set", "user", "Current household device set.", verified=False),
    Endpoint("household_invites", "Household invitations", APP + "/v1/household/users/{uid}/invitations", "user", "Pending invitations.", verified=False),
    Endpoint("household_v2", "Household (v2)", APP + "/v2/users/{uid}/household", "user", "Household details.", verified=False),
    Endpoint("performance", "Performance windows", APP + "/v1/users/{uid}/performance-windows", "user", "Exercise and focus windows.", verified=False),
    Endpoint("chronotype", "Chronotype", APP + "/v1/users/{uid}/chronotype", "user", "Chronotype details.", verified=False),
    Endpoint("hot_flash", "Hot flash", APP + "/v1/users/{uid}/hot-flash", "user", "Hot flash mode.", verified=False),
    Endpoint("snoring", "Snoring", APP + "/v1/users/{uid}/snoring", "user", "Snoring details.", verified=False),
    Endpoint("sleep_report", "Monthly report", APP + "/v1/users/{uid}/reports/monthly", "user", "Monthly sleep report.", verified=False),
    Endpoint("device_app", "Device (app API)", APP + "/v1/devices/{did}", "device", "Device record from the app API.", verified=False),
    Endpoint("priming_tasks", "Priming tasks", APP + "/v1/devices/{did}/priming/tasks", "device", "Priming history.", verified=False),
    Endpoint("priming_schedule", "Priming schedule", APP + "/v1/devices/{did}/priming/schedule", "device", "Scheduled priming.", verified=False),
    Endpoint("device_online", "Device online", CLIENT + "/devices/{did}/online", "device", "Online status.", verified=False),
    Endpoint("device_owner", "Device owner", CLIENT + "/devices/{did}/owner", "device", "Owner record.", verified=False),
    Endpoint("device_peripherals", "Peripherals", APP + "/v1/devices/{did}/peripherals", "device", "Connected covers and other peripherals.", verified=False),
    Endpoint("device_maintenance", "Maintenance", APP + "/v1/devices/{did}/maintenance", "device", "Maintenance status.", verified=False),
    Endpoint("device_led", "LED settings", APP + "/v1/devices/{did}/led", "device", "LED brightness.", verified=False),
    Endpoint("device_tap", "Tap settings", APP + "/v1/devices/{did}/tap-settings", "device", "Tap gesture settings.", verified=False),
]
BY_KEY = {e.key: e for e in CATALOG}


def load_env(path: Path = ROOT / ".env") -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


class EightClient:
    """Logs in once, then serves GET requests. Never sends anything but GET."""

    def __init__(self) -> None:
        load_env()
        self.email = os.environ.get("EIGHT_EMAIL", "")
        self.password = os.environ.get("EIGHT_PASSWORD", "")
        self.tz = os.environ.get("EIGHT_TZ", "Asia/Kolkata")
        if not self.email or not self.password:
            raise RuntimeError("EIGHT_EMAIL and EIGHT_PASSWORD are not set")
        self.http = httpx.Client(timeout=40)
        self.token = ""
        self.token_expires = 0.0
        self.device_id = ""
        self.users: dict[str, str] = {}   # side -> user id

    def _login(self) -> None:
        response = self.http.post(AUTH_URL, headers=HEADERS, json={
            "client_id": os.environ.get("EIGHT_CLIENT_ID") or KNOWN_CLIENT_ID,
            "client_secret": os.environ.get("EIGHT_CLIENT_SECRET") or KNOWN_CLIENT_SECRET,
            "grant_type": "password", "username": self.email, "password": self.password,
        })
        response.raise_for_status()
        body = response.json()
        self.token = body["access_token"]
        self.token_expires = time.time() + float(body["expires_in"]) - 120

    def get_url(self, url: str, params: dict | None = None) -> tuple[int, object]:
        """GET a URL. Returns (status code, parsed JSON or text)."""
        if time.time() >= self.token_expires:
            self._login()
        response = self.http.get(url, params=params or None, headers={**HEADERS, "authorization": f"Bearer {self.token}"})
        try:
            return response.status_code, response.json()
        except ValueError:
            return response.status_code, response.text[:500]

    def discover(self) -> None:
        """Find the Pod and which user is on which side."""
        if self.device_id:
            return
        _, me = self.get_url(CLIENT + "/users/me")
        self.device_id = me["user"]["devices"][0]
        _, device = self.get_url(CLIENT + f"/devices/{self.device_id}")
        result = device["result"]
        for side in ("left", "right"):
            uid = result.get(f"{side}UserId") or (result.get("awaySides") or {}).get(f"{side}UserId")
            if uid:
                self.users[side] = uid

    def fetch(self, key: str, side: str | None = None, days: int = 30) -> tuple[int, object]:
        """Fetch one catalog endpoint. `side` is needed for user-scoped endpoints."""
        self.discover()
        endpoint = BY_KEY[key]
        today = datetime.today()
        fill = {
            "uid": self.users.get(side or "", ""), "did": self.device_id, "tz": self.tz,
            "from": (today - timedelta(days=days)).strftime("%Y-%m-%d"),
            "to": (today + timedelta(days=1)).strftime("%Y-%m-%d"),
        }
        url = endpoint.url.format(**fill)
        params = {k: v.format(**fill) for k, v in endpoint.params.items()}
        return self.get_url(url, params)


def shape(value: object) -> str:
    if isinstance(value, dict):
        return "keys: " + ", ".join(list(value.keys())[:12])
    if isinstance(value, list):
        return f"list of {len(value)}"
    return str(value)[:80]


def probe() -> None:
    client = EightClient()
    client.discover()
    out_dir = ROOT / "out" / ("endpoints-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
    out_dir.mkdir(parents=True, exist_ok=True)
    working: list[str] = []
    for endpoint in CATALOG:
        sides = list(client.users) if endpoint.scope == "user" else [None]
        statuses = []
        for side in sides:
            status, body = client.fetch(endpoint.key, side)
            statuses.append(status)
            if status == 200:
                name = endpoint.key + (f"_{side}" if side else "")
                (out_dir / f"{name}.json").write_text(json.dumps(body, indent=1, default=str), encoding="utf-8")
                detail = shape(body)
        ok = 200 in statuses
        if ok:
            working.append(endpoint.key)
        mark = "OK  " if ok else "--  "
        print(f"{mark}{endpoint.key:20} {'/'.join(str(s) for s in statuses):9} {detail if ok else ''}"[:200])
    (ROOT / "endpoints_working.json").write_text(json.dumps(working, indent=1), encoding="utf-8")
    print(f"\n{len(working)} of {len(CATALOG)} endpoints answered. Saved to {out_dir}")


if __name__ == "__main__":
    probe()
