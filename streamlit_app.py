"""Eight Sleep insights dashboard (Streamlit).

Run:  .venv\\Scripts\\python.exe -m streamlit run streamlit_app.py
Read-only: every call to Eight Sleep is a GET made through eight_api.EightClient.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import altair as alt
import pandas as pd
import streamlit as st

from eight_api import BY_KEY, CATALOG, ROOT, EightClient

st.set_page_config(page_title="Eight Sleep Insights", page_icon=":material/bedtime:", layout="wide")

BLUE, ORANGE, AQUA, GRAY = "#2a78d6", "#eb6834", "#1baf7a", "#a9a8a1"
STAGE_ORDER = ["Out of bed", "Awake", "REM", "Light", "Deep"]
STAGE_NAMES = {"out": "Out of bed", "awake": "Awake", "rem": "REM", "light": "Light", "deep": "Deep"}
STAGE_COLORS = {"Out of bed": "#d9d8d2", "Awake": GRAY, "REM": AQUA, "Light": BLUE, "Deep": ORANGE}
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


# ----------------------------------------------------------------------------- display helpers
# Charts and tables take their accessible name from the section heading above them.
_section = {"title": "Eight Sleep data"}


def heading(text: str) -> None:
    _section["title"] = text
    st.subheader(text)


def show_chart(chart, **kwargs) -> None:
    st.altair_chart(chart, alt=f"Chart: {_section['title']}", **kwargs)


def show_table(frame, **kwargs) -> None:
    st.dataframe(frame, alt=f"Table: {_section['title']}", **kwargs)


# ----------------------------------------------------------------------------- data access
@st.cache_resource
def get_client() -> EightClient:
    client = EightClient()
    client.discover()
    return client


@st.cache_data(ttl=300, show_spinner=False)
def fetch(key: str, side: str | None = None, days: int = 30) -> tuple[int, object]:
    return get_client().fetch(key, side, days)


def data(key: str, side: str | None = None, days: int = 30):
    """Body of a working endpoint, or None when it did not answer 200."""
    status, body = fetch(key, side, days)
    return body if status == 200 else None


def working_keys() -> list[str]:
    path = ROOT / "endpoints_working.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return [e.key for e in CATALOG if e.verified]


def local(ts: str | None, tz: str):
    if not ts:
        return pd.NaT
    return pd.Timestamp(ts).tz_convert(tz)


@st.cache_data(ttl=300, show_spinner=False)
def nights_frame(side: str, days: int) -> pd.DataFrame:
    """One row per recorded night, with nightly averages of the vitals time series."""
    tz = get_client().tz
    trends = data("trends", side, days) or {}
    rows = []
    for day in trends.get("days", []):
        series: dict[str, list[float]] = {}
        for session in day.get("sessions") or []:
            for name, points in (session.get("timeseries") or {}).items():
                series.setdefault(name, []).extend(p[1] for p in points if isinstance(p[1], (int, float)))

        def mean(name: str):
            values = series.get(name)
            return sum(values) / len(values) if values else None

        start, end = local(day.get("sleepStart"), tz), local(day.get("sleepEnd"), tz)
        bed_hour = None if pd.isna(start) else start.hour + start.minute / 60
        sleep = day.get("sleepDuration") or 0
        rows.append({
            "night": pd.Timestamp(day["day"]),
            "weekday": pd.Timestamp(day["day"]).day_name(),
            "score": day.get("score"),
            "quality_score": day.get("sleepQualityScore"),
            "routine_score": day.get("sleepRoutineScore"),
            "asleep_h": sleep / 3600,
            "in_bed_h": (day.get("presenceDuration") or 0) / 3600,
            "light_h": (day.get("lightDuration") or 0) / 3600,
            "deep_h": (day.get("deepDuration") or 0) / 3600,
            "rem_h": (day.get("remDuration") or 0) / 3600,
            "awake_min": (day.get("wasoDuration") or 0) / 60,
            "deep_pct": 100 * (day.get("deepDuration") or 0) / sleep if sleep else None,
            "rem_pct": 100 * (day.get("remDuration") or 0) / sleep if sleep else None,
            "efficiency_pct": 100 * sleep / day["presenceDuration"] if day.get("presenceDuration") else None,
            "wakeups": day.get("wakeupCount"),
            "toss_turn": day.get("tnt"),
            "snore_pct": day.get("snorePercent"),
            # Hours relative to midnight, so 23:30 is -0.5 and 00:30 is +0.5.
            "bedtime_rel": None if bed_hour is None else (bed_hour - 24 if bed_hour >= 12 else bed_hour),
            "wake_hour": None if pd.isna(end) else end.hour + end.minute / 60,
            "heart_rate": mean("heartRate"),
            "hrv": mean("rmssd"),
            "breath_rate": mean("nemeanRespiratoryRate") or mean("respiratoryRate"),
            "bed_temp": mean("tempBedC"),
            "room_temp": mean("tempRoomC"),
        })
    return pd.DataFrame(rows)


def night_sessions(side: str, days: int, night: str) -> list[dict]:
    trends = data("trends", side, days) or {}
    for day in trends.get("days", []):
        if day["day"] == night:
            return day.get("sessions") or []
    return []


# ----------------------------------------------------------------------------- formatting
def hm(hours: float | None) -> str:
    if hours is None or pd.isna(hours):
        return "–"
    minutes = round(hours * 60)
    return f"{minutes // 60}h {minutes % 60:02d}m"


def clock(rel_hours: float | None) -> str:
    if rel_hours is None or pd.isna(rel_hours):
        return "–"
    minutes = round((rel_hours % 24) * 60)
    return f"{(minutes // 60) % 24:02d}:{minutes % 60:02d}"


def num(value, digits: int = 0, suffix: str = "") -> str:
    return "–" if value is None or pd.isna(value) else f"{value:.{digits}f}{suffix}"


def delta(df: pd.DataFrame, column: str, digits: int = 0, suffix: str = ""):
    """Last 7 recorded nights against the 7 before them."""
    values = df[column].dropna()
    if len(values) < 14:
        return None
    change = values.iloc[-7:].mean() - values.iloc[-14:-7].mean()
    return f"{change:+.{digits}f}{suffix}"


# ----------------------------------------------------------------------------- charts
def line_chart(df: pd.DataFrame, column: str, title: str, color: str = BLUE, rolling: bool = True, zero: bool = False):
    frame = df[["night", column]].dropna()
    if frame.empty:
        st.caption(f"No {title.lower()} data in this period.")
        return
    base = alt.Chart(frame).encode(x=alt.X("night:T", title=None))
    tooltip = [alt.Tooltip("night:T", title="Night"), alt.Tooltip(f"{column}:Q", title=title, format=".1f")]
    layers = [base.mark_line(color=color, strokeWidth=2, opacity=0.45 if rolling else 1).encode(
        y=alt.Y(f"{column}:Q", title=title, scale=alt.Scale(zero=zero)), tooltip=tooltip)]
    layers.append(base.mark_circle(color=color, size=28, opacity=0.6).encode(y=f"{column}:Q", tooltip=tooltip))
    if rolling and len(frame) >= 7:
        frame["avg7"] = frame[column].rolling(7, min_periods=4).mean()
        layers.append(alt.Chart(frame).mark_line(color=color, strokeWidth=3).encode(
            x="night:T", y="avg7:Q", tooltip=[alt.Tooltip("night:T", title="Night"), alt.Tooltip("avg7:Q", title="7-night average", format=".1f")]))
    show_chart(alt.layer(*layers).properties(height=240), width="stretch")
    if rolling and len(frame) >= 7:
        st.caption("Thin line: each night. Thick line: 7-night average.")


def stage_bars(df: pd.DataFrame):
    frame = df.melt(id_vars="night", value_vars=["light_h", "deep_h", "rem_h"], var_name="stage", value_name="hours")
    frame["stage"] = frame["stage"].map({"light_h": "Light", "deep_h": "Deep", "rem_h": "REM"})
    chart = alt.Chart(frame).mark_bar().encode(
        x=alt.X("night:T", title=None),
        y=alt.Y("hours:Q", title="Hours asleep"),
        color=alt.Color("stage:N", title=None, scale=alt.Scale(domain=["Light", "Deep", "REM"], range=[BLUE, ORANGE, AQUA]),
                        legend=alt.Legend(orient="top")),
        order=alt.Order("stage:N", sort="descending"),
        tooltip=[alt.Tooltip("night:T", title="Night"), "stage:N", alt.Tooltip("hours:Q", format=".1f")],
    ).properties(height=260)
    show_chart(chart, width="stretch")


def scatter(df: pd.DataFrame, x: str, y: str, x_title: str, y_title: str):
    frame = df[["night", x, y]].dropna()
    if len(frame) < 5:
        st.caption("Not enough nights with both values.")
        return
    points = alt.Chart(frame).mark_circle(size=60, color=BLUE, opacity=0.65).encode(
        x=alt.X(f"{x}:Q", title=x_title, scale=alt.Scale(zero=False)),
        y=alt.Y(f"{y}:Q", title=y_title, scale=alt.Scale(zero=False)),
        tooltip=[alt.Tooltip("night:T", title="Night"), alt.Tooltip(f"{x}:Q", title=x_title, format=".1f"), alt.Tooltip(f"{y}:Q", title=y_title, format=".1f")],
    )
    trend = points.transform_regression(x, y).mark_line(color=ORANGE, strokeWidth=2)
    show_chart((points + trend).properties(height=300), width="stretch")


def series_chart(sessions: list[dict], names: dict[str, str], title: str, tz: str, colors: list[str]):
    rows = []
    for session in sessions:
        for key, label in names.items():
            for point in (session.get("timeseries") or {}).get(key) or []:
                if isinstance(point[1], (int, float)):
                    rows.append({"time": local(point[0], tz).tz_localize(None), "series": label, "value": point[1]})
    if not rows:
        st.caption(f"No {title.lower()} readings for this night.")
        return
    frame = pd.DataFrame(rows)
    chart = alt.Chart(frame).mark_line(strokeWidth=2).encode(
        x=alt.X("time:T", title=None, axis=alt.Axis(format="%H:%M")),
        y=alt.Y("value:Q", title=title, scale=alt.Scale(zero=False)),
        color=alt.Color("series:N", title=None, scale=alt.Scale(domain=list(names.values()), range=colors),
                        legend=alt.Legend(orient="top") if len(names) > 1 else None),
        tooltip=[alt.Tooltip("time:T", format="%H:%M", title="Time"), "series:N", alt.Tooltip("value:Q", format=".1f")],
    ).properties(height=220)
    show_chart(chart, width="stretch")


def hypnogram(sessions: list[dict], tz: str):
    rows = []
    for session in sessions:
        cursor = local(session.get("ts"), tz)
        if pd.isna(cursor):
            continue
        cursor = cursor.tz_localize(None)
        for stage in session.get("stages") or []:
            end = cursor + timedelta(seconds=stage.get("duration", 0))
            rows.append({"start": cursor, "end": end, "stage": STAGE_NAMES.get(stage.get("stage"), stage.get("stage")),
                         "minutes": round(stage.get("duration", 0) / 60)})
            cursor = end
    if not rows:
        st.caption("No stage data for this night.")
        return
    chart = alt.Chart(pd.DataFrame(rows)).mark_bar(height=22, cornerRadius=3).encode(
        x=alt.X("start:T", title=None, axis=alt.Axis(format="%H:%M")),
        x2="end:T",
        y=alt.Y("stage:N", title=None, sort=STAGE_ORDER),
        color=alt.Color("stage:N", legend=None, scale=alt.Scale(domain=STAGE_ORDER, range=[STAGE_COLORS[s] for s in STAGE_ORDER])),
        tooltip=["stage:N", alt.Tooltip("start:T", format="%H:%M", title="From"), alt.Tooltip("end:T", format="%H:%M", title="To"), alt.Tooltip("minutes:Q", title="Minutes")],
    ).properties(height=190)
    show_chart(chart, width="stretch")


# ----------------------------------------------------------------------------- computed insights
FACTORS = {
    "asleep_h": "time asleep", "bedtime_rel": "a later bedtime", "room_temp": "a warmer room", "bed_temp": "a warmer bed",
    "heart_rate": "a higher heart rate", "hrv": "higher HRV", "breath_rate": "faster breathing", "toss_turn": "more tossing and turning",
    "snore_pct": "more snoring", "wakeups": "more wake-ups", "deep_pct": "more deep sleep", "rem_pct": "more REM sleep",
}


def correlations(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for column, label in FACTORS.items():
        pair = df[["score", column]].dropna()
        if len(pair) >= 10 and pair[column].std() > 0 and pair["score"].std() > 0:
            rows.append({"factor": label, "column": column, "r": pair["score"].corr(pair[column]), "nights": len(pair)})
    if not rows:
        return pd.DataFrame(columns=["factor", "column", "r", "nights"])
    return pd.DataFrame(rows).sort_values("r", key=abs, ascending=False)


def insight_lines(df: pd.DataFrame) -> list[str]:
    lines = []
    if len(df) < 7:
        return ["Fewer than 7 recorded nights in this period, which is too few to compute patterns."]
    lines.append(f"Average sleep score is **{df['score'].mean():.0f}**, ranging from {df['score'].min():.0f} to {df['score'].max():.0f} across {len(df)} nights.")
    seven = (df["asleep_h"] >= 7).mean() * 100
    lines.append(f"Time asleep averages **{hm(df['asleep_h'].mean())}**; {seven:.0f}% of nights reached 7 hours or more.")
    bed = df["bedtime_rel"].dropna()
    if len(bed) >= 7:
        lines.append(f"Typical time of falling asleep is **{clock(bed.median())}**, varying by about ±{bed.std() * 60:.0f} minutes night to night.")
    eff = df["efficiency_pct"].dropna()
    if len(eff):
        lines.append(f"About **{eff.mean():.0f}%** of the time in bed is spent asleep.")
    week = df.groupby("weekday")["score"].mean().reindex(WEEKDAYS).dropna()
    if len(week) >= 5:
        lines.append(f"Best-scoring weekday is **{week.idxmax()}** ({week.max():.0f}); lowest is **{week.idxmin()}** ({week.min():.0f}).")
    if len(df) >= 14:
        change = df["score"].iloc[-7:].mean() - df["score"].iloc[-14:-7].mean()
        direction = "up" if change > 0 else "down"
        lines.append(f"The last 7 recorded nights average **{abs(change):.0f} points {direction}** on the 7 before them.")
    for _, row in correlations(df).head(3).iterrows():
        if abs(row["r"]) >= 0.3:
            way = "higher" if row["r"] > 0 else "lower"
            lines.append(f"Nights with {row['factor']} tend to have **{way} scores** (correlation {row['r']:+.2f} over {row['nights']} nights).")
    return lines


# ----------------------------------------------------------------------------- sidebar
client = get_client()
with st.sidebar:
    st.title(":material/bedtime: Eight Sleep")
    side = st.segmented_control("Bed side", ["left", "right"], default="left", format_func=str.capitalize) or "left"
    days = st.select_slider("Period", options=[30, 60, 120, 180, 365], value=120, format_func=lambda d: f"{d} days")
    if st.button("Reload data", icon=":material/refresh:", width="stretch"):
        st.cache_data.clear()
        st.rerun()
    st.caption("Read-only. Data comes from Eight Sleep's cloud and is cached for 5 minutes. "
               "Patterns shown here are computed by this dashboard, not by Eight Sleep.")

df = nights_frame(side, days)
tz = client.tz

st.title(f"Sleep insights · {side.capitalize()} side")
if df.empty:
    st.warning(f"No sleep was recorded for the {side} side in the last {days} days.", icon=":material/info:")
else:
    last = df["night"].max()
    gap = (pd.Timestamp(datetime.today().date()) - last).days
    if gap >= 3:
        st.warning(f"Last recorded night is {last:%d %b %Y}. No sleep data has been recorded for this side in the {gap} days since.", icon=":material/info:")

tabs = st.tabs(["Overview", "Trends", "Patterns", "Night detail", "Temperature & schedule", "Pod & household", "API explorer"])

# ----------------------------------------------------------------------------- overview
with tabs[0]:
    if df.empty:
        st.info("Nothing to summarise for this side and period.")
    else:
        with st.container(horizontal=True):
            st.metric("Sleep score", num(df["score"].mean()), delta(df, "score"), border=True)
            st.metric("Time asleep", hm(df["asleep_h"].mean()), delta(df, "asleep_h", 1, " h"), border=True)
            st.metric("Deep sleep", num(df["deep_pct"].mean(), 0, "%"), delta(df, "deep_pct", 0, " pts"), border=True)
            st.metric("REM sleep", num(df["rem_pct"].mean(), 0, "%"), delta(df, "rem_pct", 0, " pts"), border=True)
        with st.container(horizontal=True):
            st.metric("Heart rate", num(df["heart_rate"].mean(), 0, " bpm"), delta(df, "heart_rate", 0, " bpm"), delta_color="inverse", border=True)
            st.metric("HRV (RMSSD)", num(df["hrv"].mean(), 0, " ms"), delta(df, "hrv", 0, " ms"), border=True)
            st.metric("Breathing", num(df["breath_rate"].mean(), 1, " /min"), delta(df, "breath_rate", 1), delta_color="off", border=True)
            st.metric("Nights recorded", len(df), border=True)
        st.caption("Averages over the selected period. Arrows compare the last 7 recorded nights with the 7 before them.")

        left, right = st.columns([3, 2])
        with left:
            heading("Sleep score")
            line_chart(df, "score", "Sleep score")
        with right:
            heading("What the data shows")
            for line in insight_lines(df):
                st.markdown(f"- {line}")
            st.caption("Correlations show what tends to occur together, not what causes what. "
                       "Longer nights, for example, contain more of everything, including movement.")

        heading("Both sides compared")
        rows = []
        for other in ("left", "right"):
            frame = nights_frame(other, days)
            if frame.empty:
                rows.append({"Side": other.capitalize(), "Nights": 0})
                continue
            rows.append({
                "Side": other.capitalize(), "Nights": len(frame), "Score": round(frame["score"].mean()),
                "Asleep": hm(frame["asleep_h"].mean()), "In bed": hm(frame["in_bed_h"].mean()),
                "Deep %": round(frame["deep_pct"].mean()), "REM %": round(frame["rem_pct"].mean()),
                "Heart rate": round(frame["heart_rate"].mean()), "HRV": round(frame["hrv"].mean()),
                "Falls asleep": clock(frame["bedtime_rel"].median()), "Wakes": clock(frame["wake_hour"].median()),
            })
        show_table(pd.DataFrame(rows), hide_index=True, width="stretch")

# ----------------------------------------------------------------------------- trends
with tabs[1]:
    if df.empty:
        st.info("No nights to chart.")
    else:
        heading("Hours asleep by stage")
        stage_bars(df)
        a, b = st.columns(2)
        with a:
            heading("Heart rate (bpm)")
            line_chart(df, "heart_rate", "Heart rate")
            heading("Breathing rate (per min)")
            line_chart(df, "breath_rate", "Breathing rate")
            heading("Bed temperature (°C)")
            line_chart(df, "bed_temp", "Bed temperature", ORANGE)
            heading("Toss and turn events")
            line_chart(df, "toss_turn", "Toss and turn", GRAY)
        with b:
            heading("HRV, RMSSD (ms)")
            line_chart(df, "hrv", "HRV")
            heading("Time awake after falling asleep (min)")
            line_chart(df, "awake_min", "Minutes awake", GRAY)
            heading("Room temperature (°C)")
            line_chart(df, "room_temp", "Room temperature", ORANGE)
            heading("Snoring (% of sleep)")
            line_chart(df, "snore_pct", "Snoring %", GRAY)

# ----------------------------------------------------------------------------- patterns
with tabs[2]:
    if len(df) < 7:
        st.info("At least 7 recorded nights are needed to look for patterns.")
    else:
        a, b = st.columns(2)
        with a:
            heading("Score by weekday")
            week = df.groupby("weekday").agg(score=("score", "mean"), nights=("score", "size")).reindex(WEEKDAYS).dropna().reset_index()
            show_chart(alt.Chart(week).mark_bar(color=BLUE, cornerRadiusTopLeft=4, cornerRadiusTopRight=4).encode(
                x=alt.X("weekday:N", sort=WEEKDAYS, title=None, axis=alt.Axis(labelAngle=0)),
                y=alt.Y("score:Q", title="Average score"),
                tooltip=["weekday:N", alt.Tooltip("score:Q", format=".0f"), "nights:Q"]).properties(height=260), width="stretch")
        with b:
            heading("Sleep timing")
            timing = df[["night", "bedtime_rel", "wake_hour"]].dropna()
            timing = timing.assign(asleep=timing["bedtime_rel"], awake=timing["wake_hour"])
            timing = timing.melt(id_vars="night", value_vars=["asleep", "awake"], var_name="event", value_name="hour")
            timing["event"] = timing["event"].map({"asleep": "Fell asleep", "awake": "Woke up"})
            timing["clock"] = timing["hour"].map(clock)
            show_chart(alt.Chart(timing).mark_circle(size=46, opacity=0.7).encode(
                x=alt.X("night:T", title=None),
                y=alt.Y("hour:Q", title="Hours from midnight", scale=alt.Scale(zero=False)),
                color=alt.Color("event:N", title=None, scale=alt.Scale(domain=["Fell asleep", "Woke up"], range=[BLUE, ORANGE]), legend=alt.Legend(orient="top")),
                tooltip=[alt.Tooltip("night:T", title="Night"), "event:N", alt.Tooltip("clock:N", title="Time")]).properties(height=260), width="stretch")
            st.caption("Negative values are before midnight, so −1 is 23:00.")

        heading("What moves the sleep score")
        corr = correlations(df)
        if corr.empty:
            st.caption("Not enough nights to compute correlations.")
        else:
            c1, c2 = st.columns([2, 3])
            with c1:
                show = corr.assign(Factor=corr["factor"].str.capitalize(), Correlation=corr["r"].round(2), Nights=corr["nights"])[["Factor", "Correlation", "Nights"]]
                show_table(show, hide_index=True, width="stretch",
                             column_config={"Correlation": st.column_config.ProgressColumn("Correlation with score", min_value=-1, max_value=1, format="%.2f")})
                st.caption("From −1 to +1. Positive: more of the factor goes with a higher score. A correlation is not a cause.")
            with c2:
                options = dict(zip(corr["factor"].str.capitalize(), corr["column"]))
                pick = st.selectbox("Plot against score", list(options))
                scatter(df, options[pick], "score", pick, "Sleep score")

        heading("Best and worst nights")
        cols = {"night": "Night", "score": "Score", "asleep_h": "Asleep (h)", "deep_pct": "Deep %", "rem_pct": "REM %", "heart_rate": "Heart rate", "hrv": "HRV", "room_temp": "Room °C"}
        ranked = df.sort_values("score", ascending=False)[list(cols)].rename(columns=cols)
        ranked["Night"] = ranked["Night"].dt.strftime("%a %d %b")
        ranked = ranked.round(1)
        x, y = st.columns(2)
        x.markdown("**Top 5**")
        x.dataframe(ranked.head(5), hide_index=True, width="stretch")
        y.markdown("**Bottom 5**")
        y.dataframe(ranked.tail(5).iloc[::-1], hide_index=True, width="stretch")

# ----------------------------------------------------------------------------- night detail
with tabs[3]:
    if df.empty:
        st.info("No nights recorded.")
    else:
        choices = df.sort_values("night", ascending=False)
        labels = {row.night.strftime("%Y-%m-%d"): f"{row.night:%a %d %b %Y} · score {num(row.score)}" for row in choices.itertuples()}
        night = st.selectbox("Night", list(labels), format_func=labels.get)
        row = df[df["night"] == pd.Timestamp(night)].iloc[0]
        sessions = night_sessions(side, days, night)
        with st.container(horizontal=True):
            st.metric("Score", num(row["score"]), border=True)
            st.metric("Asleep", hm(row["asleep_h"]), border=True)
            st.metric("In bed", hm(row["in_bed_h"]), border=True)
            st.metric("Fell asleep", clock(row["bedtime_rel"]), border=True)
            st.metric("Woke up", clock(row["wake_hour"]), border=True)
            st.metric("Wake-ups", num(row["wakeups"]), border=True)
        heading("Sleep stages")
        hypnogram(sessions, tz)
        a, b = st.columns(2)
        with a:
            heading("Heart rate (bpm)")
            series_chart(sessions, {"heartRate": "Heart rate"}, "Heart rate", tz, [BLUE])
            heading("Breathing rate (per min)")
            series_chart(sessions, {"nemeanRespiratoryRate": "Breathing"}, "Breathing rate", tz, [BLUE])
        with b:
            heading("HRV, RMSSD (ms)")
            series_chart(sessions, {"rmssd": "RMSSD"}, "HRV", tz, [BLUE])
            heading("Temperature (°C)")
            series_chart(sessions, {"tempBedC": "Bed", "tempRoomC": "Room"}, "Temperature", tz, [BLUE, ORANGE])

# ----------------------------------------------------------------------------- temperature & schedule
with tabs[4]:
    temp = data("temperature", side) or {}
    auto = data("autopilot", side) or {}
    away = data("away_mode", side) or {}
    state = (temp.get("currentState") or {}).get("type", "–")
    with st.container(horizontal=True):
        st.metric("Current state", str(state).capitalize(), border=True)
        st.metric("Set level", num(temp.get("currentLevel")), border=True)
        st.metric("Measured level", num(temp.get("currentDeviceLevel")), border=True)
        st.metric("Autopilot", "Active" if auto.get("isAutopilotActive") else "Off", border=True)
        st.metric("Away mode", "On" if away.get("isAway") else "Off", border=True)
    st.caption("Levels run from −100 (coldest) to +100 (warmest).")

    a, b = st.columns(2)
    with a:
        heading("Temperature level by sleep phase")
        phases = {"bedTimeLevel": "Bedtime", "initialSleepLevel": "Early sleep", "finalSleepLevel": "Late sleep"}
        rows = [{"phase": label, "who": "Your setting", "level": (temp.get("smart") or {}).get(key)} for key, label in phases.items()]
        suggestions = (data("level_suggestions", side) or {}).get("levelSuggestions") or []
        cohort = next((s for s in suggestions if s.get("type") == "cohort"), None)
        if cohort:
            levels = (cohort.get("data") or {}).get("levels") or {}
            rows += [{"phase": label, "who": "Similar sleepers", "level": levels.get(key)} for key, label in phases.items()]
        frame = pd.DataFrame(rows).dropna()
        if frame.empty:
            st.caption("No smart temperature levels set.")
        else:
            show_chart(alt.Chart(frame).mark_bar(cornerRadius=4).encode(
                x=alt.X("who:N", title=None, axis=None), y=alt.Y("level:Q", title="Level"),
                color=alt.Color("who:N", title=None, scale=alt.Scale(domain=["Your setting", "Similar sleepers"], range=[BLUE, ORANGE]), legend=alt.Legend(orient="top")),
                column=alt.Column("phase:N", title=None, sort=list(phases.values())),
                tooltip=["phase:N", "who:N", "level:Q"]).properties(height=220, width=110))
            if cohort:
                group = (cohort.get("data") or {}).get("cohort") or {}
                st.caption(f"'Similar sleepers' is Eight Sleep's suggestion for its cohort: {group.get('genderGroup', '–')}, age group {group.get('ageGroup', '–')}, {group.get('temperatureGroup', '–')} sleepers.")
    with b:
        heading("Autopilot")
        mode = auto.get("userMode") or {}
        calibration = auto.get("calibrationStatus") or {}
        snore = auto.get("snoringMitigation") or {}
        show_table(pd.DataFrame([
            ("Mode", mode.get("autopilotMode", "–")),
            ("Adjusts for room temperature", "Yes" if (mode.get("autopilotOptions") or {}).get("ambientTempEnabled") else "No"),
            ("Calibrated", "Yes" if calibration.get("isCalibrated") else f"{calibration.get('calibrationDaysCompleted', 0)} of {calibration.get('daysRequiredForCalibration', '–')} days"),
            ("Subscription active", "Yes" if auto.get("hasActiveSubscription") else "No"),
            ("Snoring mitigation", "On" if snore.get("enabled") else "Off"),
            ("Sleep position setting", snore.get("sleepStyle", "–")),
        ], columns=["Setting", "Value"]), hide_index=True, width="stretch")

    heading("Bedtime schedules")
    device = (data("device") or {}).get("result") or {}
    profiles = (device.get(f"{side}Kelvin") or {}).get("scheduleProfiles") or []
    if profiles:
        show_table(pd.DataFrame([{
            "Bedtime": p.get("startLocalTime", "")[:5], "Enabled": bool(p.get("enabled")),
            "Days": ", ".join(d[:3].capitalize() for d in ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"] if (p.get("weekDays") or {}).get(d)),
        } for p in profiles]), hide_index=True, width="stretch")
    else:
        st.caption("No bedtime schedule set for this side.")

    heading("Alarms")
    alarms = (data("alarms", side) or {}).get("alarms") or []
    if alarms:
        show_table(pd.DataFrame([{
            "Time": a.get("time", "")[:5], "Enabled": bool(a.get("enabled")),
            "Repeats": ", ".join(d[:3].capitalize() for d, on in ((a.get("repeat") or {}).get("weekDays") or {}).items() if on) if (a.get("repeat") or {}).get("enabled") else "One-off",
            "Vibration": f"{(a.get('vibration') or {}).get('powerLevel', '–')}% {(a.get('vibration') or {}).get('pattern', '').lower()}" if (a.get("vibration") or {}).get("enabled") else "Off",
            "Thermal": f"level {(a.get('thermal') or {}).get('level', '–')}" if (a.get("thermal") or {}).get("enabled") else "Off",
            "Wake in light sleep": bool((a.get("smart") or {}).get("lightSleepEnabled")),
        } for a in alarms]), hide_index=True, width="stretch")
    else:
        st.caption("No alarms saved for this side.")

# ----------------------------------------------------------------------------- pod & household
with tabs[5]:
    device = (data("device") or {}).get("result") or {}
    wifi = device.get("wifiInfo") or {}
    sensor = device.get("sensorInfo") or {}
    with st.container(horizontal=True):
        st.metric("Pod", device.get("modelString", "–"), border=True)
        st.metric("Connection", "Online" if device.get("online") else "Offline", border=True)
        st.metric("WiFi signal", num(wifi.get("signalStrength"), 0, " dBm"), border=True)
        st.metric("Water", "OK" if device.get("hasWater") else "Low", border=True)
        st.metric("Cover", "Connected" if sensor.get("connected") else "Not connected", border=True)
    a, b = st.columns(2)
    with a:
        heading("Device")
        show_table(pd.DataFrame([
            ("Last heard", f"{local(device.get('lastHeard'), tz):%d %b %H:%M}" if device.get("lastHeard") else "–"),
            ("WiFi network", wifi.get("ssid", "–")), ("IP address", wifi.get("ipAddr", "–")), ("MAC address", wifi.get("macAddr", "–")),
            ("Firmware", device.get("firmwareVersion", "–")), ("Hub serial", device.get("hubSerial", "–")),
            ("Cover serial", sensor.get("serialNumber", "–")), ("Bed size", (device.get("bedSize") or {}).get("value", "–")),
            ("Features", ", ".join(device.get("features") or [])),
        ], columns=["Item", "Value"]), hide_index=True, width="stretch")
    with b:
        heading("Household")
        household = data("household_summary", side) or {}
        rows = []
        for home in household.get("households") or []:
            for group in home.get("sets") or []:
                for item in group.get("devices") or []:
                    rows.append({"Household": home.get("householdName"), "Set": group.get("setName"), "Device": item.get("deviceName"),
                                 "Type": item.get("specialization"), "Members in household": len(home.get("users") or [])})
        if rows:
            show_table(pd.DataFrame(rows), hide_index=True, width="stretch")
            st.caption("This dashboard reads the first Pod on the account only.")
        else:
            st.caption("No household details returned.")

    heading("Priming history")
    tasks = (data("priming_tasks") or {}).get("tasks") or []
    if tasks:
        frame = pd.DataFrame([{
            "started": local(t.get("started") or t.get("created"), tz).tz_localize(None),
            "minutes": (pd.Timestamp(t["finished"]) - pd.Timestamp(t["started"])).total_seconds() / 60 if t.get("finished") and t.get("started") else None,
            "outcome": t.get("outcome") or t.get("status") or "unknown", "reason": t.get("reason", "–"),
        } for t in tasks]).dropna(subset=["started"])
        done = (frame["outcome"] == "succeeded").sum()
        with st.container(horizontal=True):
            st.metric("Priming runs", len(frame), border=True)
            st.metric("Succeeded", f"{done} of {len(frame)}", border=True)
            st.metric("Typical duration", num(frame["minutes"].median(), 0, " min"), border=True)
            st.metric("Last run", f"{frame['started'].max():%d %b %Y}", border=True)
        outcomes = sorted(frame["outcome"].unique())
        show_chart(alt.Chart(frame.dropna(subset=["minutes"])).mark_circle(size=60, opacity=0.75).encode(
            x=alt.X("started:T", title=None), y=alt.Y("minutes:Q", title="Duration (minutes)"),
            color=alt.Color("outcome:N", title=None, scale=alt.Scale(domain=outcomes, range=[BLUE, ORANGE, AQUA, GRAY][:len(outcomes)]), legend=alt.Legend(orient="top")),
            tooltip=[alt.Tooltip("started:T", title="Started", format="%d %b %Y %H:%M"), alt.Tooltip("minutes:Q", format=".0f"), "outcome:N", "reason:N"]).properties(height=240), width="stretch")
        schedule = (data("priming_schedule") or {}).get("schedule") or []
        if schedule:
            st.caption("Scheduled priming: " + ", ".join(schedule))
    else:
        st.caption("No priming history returned.")

    heading("Sleep streak challenges")
    challenges = (data("challenges", side) or {}).get("results") or []
    if challenges:
        show_table(pd.DataFrame([{
            "Started": (c.get("definition") or {}).get("startDate"), "Type": c.get("type"), "State": c.get("state"),
            "Days reached": (c.get("progress") or {}).get("dayCount"), "Next milestone": (c.get("progress") or {}).get("nextMilestoneDayCount"),
            "Target": (c.get("definition") or {}).get("target", {}).get("value"),
        } for c in challenges]), hide_index=True, width="stretch")
        st.caption("Target PT25200S means 7 hours of sleep per night.")
    else:
        st.caption("No challenges returned for this side.")

# ----------------------------------------------------------------------------- api explorer
with tabs[6]:
    heading("Every endpoint this dashboard knows")
    live = set(working_keys())
    st.caption(f"{len(live)} of {len(CATALOG)} candidate endpoints answered when last probed. All requests are GET. "
               "Re-run `python eight_api.py` to probe again.")
    table = pd.DataFrame([{
        "Works": e.key in live, "Name": e.title, "Scope": e.scope,
        "Path": e.url.replace("https://client-api.8slp.net", "client-api").replace("https://app-api.8slp.net", "app-api"),
        "What it returns": e.about,
    } for e in sorted(CATALOG, key=lambda e: (e.key not in live, e.scope, e.title))])
    show_table(table, hide_index=True, width="stretch", height=420)

    heading("Raw response")
    options = [e for e in CATALOG if e.key in live]
    endpoint = st.selectbox("Endpoint", options, format_func=lambda e: f"{e.title} ({e.scope})")
    scope_side = side if endpoint.scope == "user" else None
    status, body = fetch(endpoint.key, scope_side, days)
    st.caption(f"{endpoint.about} HTTP {status}" + (f" · {side} side" if scope_side else ""))
    if st.toggle("Hide personal fields", value=True):
        hidden = {"email", "firstName", "lastName", "dob", "zip", "gender"}

        def scrub(value):
            if isinstance(value, dict):
                return {k: ("•••" if k in hidden else scrub(v)) for k, v in value.items()}
            if isinstance(value, list):
                return [scrub(v) for v in value]
            return value

        body = scrub(body)
    st.json(body, expanded=2)
    st.download_button("Download JSON", json.dumps(body, indent=2, default=str), file_name=f"{endpoint.key}.json", icon=":material/download:")
