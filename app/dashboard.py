"""FocusGuard AI dashboard (Streamlit).

    .venv/bin/streamlit run app/dashboard.py
or "Open dashboard" in the FocusGuard AI menu-bar icon / extension popup.

Reads everything from the FocusGuard AI agent on this computer. Actions (start a
focus session, add a task, change a site) are sent to the agent, which hands
them to the browser extension within a few seconds.
"""

from __future__ import annotations

import datetime as dt
import io
import time
import zipfile

import altair as alt
import pandas as pd
import streamlit as st

import dg_api as api

st.set_page_config(page_title="FocusGuard AI", page_icon="🛟", layout="wide", initial_sidebar_state="auto")

BUOY = "#D97F1E"
TIDE = "#1F7A74"
TIDE_TEXT = "#2B8F86"
FAINT = "#7B908D"

st.html("""
<style>
  .block-container { padding-top: 2.2rem; max-width: 1180px; }
  .dg-hero { font-size: clamp(26px, 3.1vw, 38px); font-weight: 650; line-height: 1.18; letter-spacing: -0.018em; margin: 0 0 .35rem; max-width: 34ch; }
  .dg-hero .bad { color: #D97F1E; }
  .dg-hero .good { color: #2B8F86; }
  .dg-sub { font-size: 1.02rem; opacity: .78; margin-bottom: 1.2rem; }
  .dg-sub b { opacity: 1; }
  .dg-kicker { font-size: .92rem; opacity: .7; margin-bottom: .2rem; }
  .dg-focus-clock { font-size: 3.2rem; font-weight: 600; font-variant-numeric: tabular-nums; letter-spacing: -0.02em; line-height: 1; }
  .dg-quote { border-left: 4px solid #D97F1E; padding: .2rem 0 .2rem .9rem; margin: .6rem 0; opacity: .9; }
  .dg-technique h4 { margin: 0 0 .3rem; }
  .dg-dots { letter-spacing: 2px; color: #1F7A74; font-size: .8rem; }
  .dg-sidebar-brand { font-size: 1.25rem; font-weight: 700; letter-spacing: -0.01em; }
  .dg-muted { opacity: .7; font-size: .9rem; }
  /* Card headings: calm and compact so the hero sentence stays the loudest thing. */
  [data-testid="stVerticalBlockBorderWrapper"] h3 { font-size: 1.15rem; font-weight: 650; padding-top: .1rem; }
  h3 { font-size: 1.2rem !important; }
  [data-testid="stMetricValue"] { font-variant-numeric: tabular-nums; }
</style>
""")


# ---------------------------------------------------------------------------
# Data

@st.cache_data(ttl=5, show_spinner=False)
def load_state() -> dict:
    return api.call("GET", "/api/state")


@st.cache_data(ttl=5, show_spinner=False)
def load_status() -> dict:
    return api.call("GET", "/api/status")


@st.cache_data(ttl=300, show_spinner="Asking your coach…")
def load_coach(_stamp: int) -> dict:
    return api.call("POST", "/api/coach", {}, timeout=40)


@st.cache_data(show_spinner=False)
def extension_bundle() -> bytes:
    """Create a clean unpacked-extension ZIP for the Chrome install flow."""
    root = api.ROOT / "extension"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.name == ".DS_Store":
                continue
            bundle.write(path, path.relative_to(root))
    return buffer.getvalue()


def refresh():
    load_state.clear()
    load_status.clear()


def send(ctype: str, done: str, **args) -> bool:
    try:
        api.command(ctype, **args)
    except api.AgentDown:
        st.toast("Couldn't reach the FocusGuard AI app. Is it running?", icon=":material/error:")
        return False
    # Chrome applies commands on its next sync (seconds, at most ~30). Show the
    # result right away so the dashboard feels instant.
    st.session_state.setdefault("pending", []).append({"type": ctype, "args": args, "at": time.time() * 1000})
    st.toast(done, icon=":material/check_circle:")
    refresh()
    return True


def with_pending(state: dict) -> dict:
    def reflected(p) -> bool:
        a, tasks, rules = p["args"], state.get("tasks") or [], state.get("rules") or []
        if p["type"] == "add_task":
            return any(t["title"] == a["title"] for t in tasks)
        if p["type"] == "complete_task":
            return any(t["id"] == a.get("id") and t.get("done") for t in tasks) or not any(t["id"] == a.get("id") for t in tasks)
        if p["type"] == "start_focus":
            return bool(api.live_focus(state, time.time() * 1000))
        if p["type"] == "stop_focus":
            return not api.live_focus(state, time.time() * 1000)
        if p["type"] == "set_site_mode":
            return any((r["id"] == a["site"] or r["label"].lower() == a["site"].lower()) and r["mode"] == a["mode"] for r in rules)
        return True

    pend = [p for p in st.session_state.get("pending", []) if time.time() * 1000 - p["at"] < 90000 and not reflected(p)]
    st.session_state["pending"] = pend
    if not pend:
        return state
    s = {**state, "tasks": [dict(t) for t in state.get("tasks") or []], "rules": [dict(r) for r in state.get("rules") or []]}
    for p in pend:
        a = p["args"]
        if p["type"] == "add_task" and not any(t["title"] == a["title"] for t in s["tasks"]):
            parent = next((t for t in s["tasks"] if t["title"] == a.get("parent")), None)
            s["tasks"].append({"id": f"pending-{p['at']}-{a['title']}", "title": a["title"], "done": False,
                               "parentId": parent and parent["id"], "pending": True})
        elif p["type"] == "complete_task":
            for t in s["tasks"]:
                if t["id"] == a.get("id"):
                    t["done"] = True
        elif p["type"] == "start_focus":
            task = next((t for t in s["tasks"] if t["title"] == a.get("task")), None)
            s["focus"] = {"kind": "pomodoro", "startedAt": p["at"], "until": p["at"] + a["minutes"] * 60000,
                          "taskId": task and task["id"], "completedAt": None, "pending": True}
        elif p["type"] == "stop_focus":
            s["focus"] = None
        elif p["type"] == "set_site_mode":
            r = next((r for r in s["rules"] if r["id"] == a["site"] or r["label"].lower() == a["site"].lower()), None)
            if r:
                r["mode"] = a["mode"]
                r["limitMin"] = a.get("limit_minutes", r.get("limitMin"))
            else:
                s["rules"].append({"id": a["site"], "label": a["site"], "mode": a["mode"], "limitMin": a.get("limit_minutes", 15)})
    return s


def agent_down():
    st.markdown('<div class="dg-hero">FocusGuard AI isn\'t running.</div>', unsafe_allow_html=True)
    st.markdown('<div class="dg-sub">The dashboard reads from the FocusGuard AI app on this computer. Start it, then come back.</div>',
                unsafe_allow_html=True)
    st.code(".venv/bin/python agent/driftguard.py", language="bash")
    if st.button("Try again", type="primary", icon=":material/refresh:"):
        refresh()
        st.rerun()


def waiting_for_browser():
    st.markdown('<div class="dg-hero">Almost there.</div>', unsafe_allow_html=True)
    st.markdown('<div class="dg-sub">The app is running but hasn\'t heard from Chrome yet. Make sure the FocusGuard AI extension '
                'is loaded and shows <b>FocusGuard AI app connected</b> in its popup. Data appears here within a minute.</div>',
                unsafe_allow_html=True)
    if st.button("Check again", type="primary", icon=":material/refresh:"):
        refresh()
        st.rerun()


def get_data():
    """-> (state, status) or None after rendering the right empty state."""
    try:
        state = with_pending(load_state())
        status = load_status()
    except api.AgentDown:
        agent_down()
        return None
    if not state.get("days") and not state.get("tasks"):
        waiting_for_browser()
        return None
    return state, status


def hero(parts: list[tuple[str, str | None]]):
    html = "".join(f'<span class="{cls}">{text}</span>' if cls else text for text, cls in parts)
    st.markdown(f'<div class="dg-hero">{html}</div>', unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Charts

def site_bars(t: dict, height: int = 220):
    rows = [{"Site": k, "Minutes": round(v["ms"] / 60000, 1)} for k, v in t["sites"].items() if v["ms"] >= 30000]
    if not rows:
        st.caption("No time on tracked sites yet.")
        return
    df = pd.DataFrame(rows).sort_values("Minutes", ascending=False)
    chart = alt.Chart(df).mark_bar(cornerRadiusEnd=4, color=BUOY).encode(
        x=alt.X("Minutes:Q", title="minutes"),
        y=alt.Y("Site:N", sort="-x", title=None, scale=alt.Scale(paddingInner=0.35)),
        tooltip=["Site", "Minutes"],
    ).properties(height=alt.Step(34))
    st.altair_chart(chart, width="stretch")


def hour_bars(day: dict):
    hours = (day or {}).get("hours") or [0] * 24
    df = pd.DataFrame({"Hour": list(range(24)), "Minutes": [round(m / 60000, 1) for m in hours]})
    df["Label"] = df["Hour"].map(lambda h: f"{(h + 11) % 12 + 1} {'AM' if h < 12 else 'PM'}")
    chart = alt.Chart(df).mark_bar(color=BUOY, cornerRadiusTopLeft=3, cornerRadiusTopRight=3).encode(
        x=alt.X("Hour:O", title=None, axis=alt.Axis(labelExpr="datum.value % 6 == 0 ? (datum.value == 0 ? '12a' : datum.value < 12 ? datum.value + 'a' : datum.value == 12 ? '12p' : (datum.value - 12) + 'p') : ''", labelAngle=0)),
        y=alt.Y("Minutes:Q", title="minutes"),
        tooltip=[alt.Tooltip("Label:N", title="Hour"), "Minutes"],
    ).properties(height=200)
    st.altair_chart(chart, width="stretch")


# ---------------------------------------------------------------------------
# Pages

def page_today():
    data = get_data()
    if not data:
        return
    state, status = data
    today = dt.date.today().isoformat()
    t = api.totals(state, [today])
    d0 = dt.date.today()
    name = ((state.get("profile") or {}).get("name") or "").strip()
    hour = time.localtime().tm_hour
    greet = "Good morning" if hour < 12 else "Good afternoon" if hour < 18 else "Good evening"
    hello = f"{greet}, {name}. " if name else ""
    st.markdown(f'<div class="dg-kicker">{hello}{d0:%A, %B} {d0.day}</div>', unsafe_allow_html=True)
    goal = (state.get("profile") or {}).get("mainGoal")
    hero([(api.fmt(t["distract"]), "bad"), (" on distracting sites today", None)])
    st.markdown(
        f'<div class="dg-sub">FocusGuard AI turned you around <b>{api.plural(t["turned"], "time")}</b>. '
        f'You focused <b>{api.fmt(t["focus"])}</b> and finished <b>{api.plural(t["tasks"], "task")}</b>.</div>',
        unsafe_allow_html=True)
    if goal:
        st.markdown(f'<div class="dg-quote">Your goal: {goal}</div>', unsafe_allow_html=True)
    drift = status.get("drift") or {}
    current = status.get("current") or {}
    if current:
        with st.container(border=True):
            dleft, dright = st.columns([1, 3])
            with dleft:
                st.metric("Live drift", f"{drift.get('score', 0)}/100", drift.get("label", "intentional"))
            with dright:
                reason = drift.get("reason") or "; ".join(drift.get("reasons") or [])
                st.markdown(f"**Currently on {current.get('app', 'a tracked site').replace('_', ' ').title()}**")
                st.caption(reason or "FocusGuard AI is watching for a pattern, not reading your page content.")
    learning = status.get("learning") or {}
    with st.expander("What FocusGuard AI is learning", expanded=False):
        st.caption(learning.get("reason", "It will personalize interventions after a few responses."))
    try:
        plan = api.call("GET", "/api/plan")
        if plan.get("date") == today and plan.get("message") and not plan.get("pending"):
            st.info(plan["message"], icon=":material/smart_toy:")
    except api.AgentDown:
        pass

    left, right = st.columns([5, 6], gap="large")
    with left:
        focus_panel()
    with right:
        with st.container(border=True):
            st.subheader("Where your time went")
            site_bars(t)
        with st.container(border=True):
            st.subheader("Hour by hour")
            hour_bars((state.get("days") or {}).get(today))

    alerts = status.get("alerts") or []
    if alerts:
        st.subheader("Nudges today")
        labels = {None: ("waiting", "gray"), "left": ("you left", "green"), "stayed": ("you stayed", "orange"),
                  "snoozed": ("snoozed", "gray"), "dismissed": ("dismissed", "gray"), "rescued": ("rescue started", "blue"),
                  "rescue_completed": ("rescue finished", "green"), "rescue_ended_early": ("ended early", "orange")}
        for a in alerts[:6]:
            with st.container(border=True, horizontal=True, vertical_alignment="center"):
                when = time.strftime("%I:%M %p", time.localtime(a["created_at"] / 1000)).lstrip("0")
                st.markdown(f"**{a['headline']}**  \n<span class='dg-muted'>{when}</span>", unsafe_allow_html=True)
                text, color = labels.get(a.get("outcome"), (a.get("outcome") or "", "gray"))
                st.badge(text, color=color)


@st.fragment(run_every=5)
def focus_panel():
    try:
        state = with_pending(load_state())
    except api.AgentDown:
        return
    now_ms = time.time() * 1000
    f = api.live_focus(state, now_ms)
    with st.container(border=True):
        if f:
            left = f["until"] - now_ms
            task = next((x["title"] for x in state.get("tasks") or [] if x["id"] == f.get("taskId")), None)
            kind = {"pomodoro": "Focusing", "rescue": "Focus Rescue", "break": "On a break", "away": "Away from the screen"}.get(f["kind"], "Focusing")
            st.markdown(f'<div class="dg-kicker">{kind}</div>', unsafe_allow_html=True)
            mins, secs = divmod(int(left / 1000), 60)
            st.markdown(f'<div class="dg-focus-clock">{mins}:{secs:02d}</div>', unsafe_allow_html=True)
            total = f["until"] - f["startedAt"]
            st.progress(min(1.0, max(0.0, 1 - left / total)))
            if task:
                st.markdown(f"**{task}**")
            elif f.get("label"):
                st.markdown(f"**{f['label']}**")
            if f.get("pending"):
                st.caption("Starting in Chrome…")
            st.caption("Every distracting site is blocked until the timer ends." if f["kind"] != "break"
                       else "Feeds set to Block stay closed during your break.")
            if st.button("Stop session", icon=":material/stop_circle:"):
                send("stop_focus", "Stopping your session.")
                st.rerun()
        else:
            st.subheader("Start a focus session")
            st.caption("Pick what you'll work on. Every distracting site is blocked until the timer ends.")
            tasks = api.open_tasks(state)
            options = ["No specific task"] + [x["title"] for x in tasks]
            choice = st.selectbox("Focus on", options, key="focus_task")
            minutes = st.segmented_control("Minutes", [15, 25, 45, 60], default=25, key="focus_minutes",
                                           format_func=lambda m: f"{m} min") or 25
            if st.button(f"Start {minutes}-minute focus", type="primary", icon=":material/play_arrow:", width="stretch"):
                task = None if choice == "No specific task" else choice
                if send("start_focus", "Starting your focus session in Chrome.", minutes=minutes, task=task):
                    time.sleep(1.5)
                    st.rerun()


def page_report():
    data = get_data()
    if not data:
        return
    state, _ = data
    n = st.segmented_control("Period", [7, 14, 30], default=7, format_func=lambda d: f"{d} days",
                             key="report_range", label_visibility="collapsed") or 7
    keys = api.last_days(n)
    prev = api.last_days(n, dt.date.today() - dt.timedelta(days=n))
    t, p = api.totals(state, keys), api.totals(state, prev)

    change = ""
    if p["distract"] > 60000:
        pct = round((t["distract"] - p["distract"]) / p["distract"] * 100)
        change = f"{abs(pct)}% {'less' if pct < 0 else 'more'}"
        cls = "good" if pct < 0 else "bad"
    parts = [(api.fmt(t["distract"]), "bad"), (f" on distracting sites in the last {n} days", None)]
    parts += [(", ", None), (change, cls), (f" than the {n} days before.", None)] if change else [(".", None)]
    hero(parts)

    c = st.columns(4)
    c[0].metric("Turned around", t["turned"], delta=t["turned"] - p["turned"] if p["turned"] else None,
                delta_color="off", border=True, help="Times a blocked site sent you to your Focus page.")
    focus_delta = None
    if p["focus"]:
        focus_delta = ("+" if t["focus"] >= p["focus"] else "-") + api.fmt_short(abs(t["focus"] - p["focus"]))
    c[1].metric("Focused", api.fmt_short(t["focus"]), delta=focus_delta, border=True)
    c[2].metric("Focus sessions", t["pomodoros"], delta=t["pomodoros"] - p["pomodoros"] if p["pomodoros"] else None, border=True)
    c[3].metric("Tasks done", t["tasks"], delta=t["tasks"] - p["tasks"] if p["tasks"] else None, border=True)

    days = state.get("days") or {}
    left, right = st.columns([7, 5], gap="large")
    with left:
        with st.container(border=True):
            st.subheader("Each day")
            st.caption("Time on distracting sites next to time spent focusing.")
            rows = []
            for k in keys:
                d = days.get(k) or {}
                dd = dt.date.fromisoformat(k)
                label = f"{dd:%a} {dd.day}" if n <= 14 else f"{dd.day}"
                rows.append({"Day": label, "order": k, "Kind": "Distracting sites", "Minutes": round(api.distract_ms(d) / 60000)})
                rows.append({"Day": label, "order": k, "Kind": "Focus", "Minutes": round(d.get("focusMs", 0) / 60000)})
            df = pd.DataFrame(rows)
            chart = alt.Chart(df).mark_bar(cornerRadiusTopLeft=3, cornerRadiusTopRight=3).encode(
                x=alt.X("Day:N", sort=alt.SortField("order"), title=None, axis=alt.Axis(labelAngle=0)),
                xOffset="Kind:N",
                y=alt.Y("Minutes:Q", title="minutes"),
                color=alt.Color("Kind:N", scale=alt.Scale(domain=["Distracting sites", "Focus"], range=[BUOY, TIDE]),
                                legend=alt.Legend(orient="bottom", title=None)),
                tooltip=["Day", "Kind", "Minutes"],
            ).properties(height=260)
            st.altair_chart(chart, width="stretch")
    with right:
        with st.container(border=True):
            st.subheader("What pulled you in")
            st.caption("Your answers to “What pulled you here?” on the Focus page.")
            if sum(t["reasons"].values()):
                df = pd.DataFrame([{"Reason": api.REASONS.get(k, k), "Times": v} for k, v in t["reasons"].items() if v])
                chart = alt.Chart(df).mark_bar(color=TIDE, cornerRadiusEnd=4).encode(
                    x=alt.X("Times:Q", title=None, axis=alt.Axis(tickMinStep=1)),
                    y=alt.Y("Reason:N", sort="-x", title=None, scale=alt.Scale(paddingInner=0.35)),
                    tooltip=["Reason", "Times"],
                ).properties(height=alt.Step(40))
                st.altair_chart(chart, width="stretch")
                top = max(t["reasons"].items(), key=lambda x: x[1])[0]
                tip = {"avoiding": "Most pulls come from avoiding something. Use Break down on your heaviest task and give step one five minutes.",
                       "bored": "Boredom is your biggest trigger. Keep two or three 10-minute things you enjoy on your task list.",
                       "tired": "You reach for feeds when tired. A real 5-minute break away from the screen restores more.",
                       "habit": "It's mostly autopilot. Blocking is doing the heavy lifting; keep the worst sites on Block.",
                       "stress": "Stress pulls you in most. Try the two-minute stress reset in the Coach tab before reaching for a feed."}[top] if top in api.REASONS else ""
                if tip:
                    st.caption(tip)
            else:
                st.caption("Nothing yet. Next time a blocked site sends you to the Focus page, tap a reason.")

    with st.container(border=True):
        st.subheader("When you drift")
        st.caption("Minutes on distracting sites by weekday and hour. Darker means more.")
        grid = {}
        for k in keys:
            d = days.get(k) or {}
            wd = dt.date.fromisoformat(k).strftime("%a")
            for h, ms in enumerate((d.get("hours") or [0] * 24)[:24]):
                grid[(wd, h)] = grid.get((wd, h), 0) + ms
        heat = pd.DataFrame([{"Weekday": wd, "Hour": h, "Minutes": round(ms / 60000)} for (wd, h), ms in grid.items()])
        if heat.empty or heat["Minutes"].sum() == 0:
            st.caption("Not enough data yet.")
        else:
            order = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
            hm = alt.Chart(heat).mark_rect(cornerRadius=3).encode(
                x=alt.X("Hour:O", title=None, axis=alt.Axis(labelExpr="datum.value % 3 == 0 ? (datum.value == 0 ? '12a' : datum.value < 12 ? datum.value + 'a' : datum.value == 12 ? '12p' : (datum.value - 12) + 'p') : ''", labelAngle=0)),
                y=alt.Y("Weekday:N", sort=order, title=None),
                color=alt.Color("Minutes:Q", scale=alt.Scale(range=["#E6EDEA", "#F3C58E", BUOY, "#8E4A0E"]), legend=None),
                tooltip=["Weekday", "Hour", "Minutes"],
            ).properties(height=alt.Step(30))
            st.altair_chart(hm, width="stretch")

    with st.container(border=True):
        st.subheader("By site")
        rows = sorted(t["sites"].items(), key=lambda x: -x[1]["ms"])
        rows = [(k, v) for k, v in rows if v["ms"] >= 30000 or v["turned"]]
        if rows:
            total = max(t["distract"], 1)
            df = pd.DataFrame([{"Site": k, "Time": api.fmt(v["ms"]), "Share of time": v["ms"] / total, "Visits": v["visits"],
                                "Shorts watched": v["shorts"] or None, "Turned around": v["turned"] or None}
                               for k, v in rows])
            st.dataframe(df, hide_index=True, width="stretch", column_config={
                "Share of time": st.column_config.ProgressColumn("Share of time", format="percent", min_value=0, max_value=1),
            })
        else:
            st.caption("No time on tracked sites in this period.")


def page_tasks():
    data = get_data()
    if not data:
        return
    state, _ = data
    hero([("What you'd rather be doing.", None)])
    st.markdown('<div class="dg-sub">Tasks live on your Focus page in Chrome too. Blocked sites send you here instead of the feed.</div>',
                unsafe_allow_html=True)

    with st.form("add_task", clear_on_submit=True, border=False):
        c1, c2 = st.columns([6, 1], vertical_alignment="bottom")
        title = c1.text_input("New task", placeholder="Add a task, then press Enter", label_visibility="collapsed")
        if c2.form_submit_button("Add", type="primary", width="stretch") and title.strip():
            send("add_task", f"Added “{title.strip()}”.", title=title.strip())
            time.sleep(1)
            st.rerun()

    tasks = api.open_tasks(state)
    if not tasks:
        st.info("No tasks yet. Start with the one thing you've been putting off.", icon=":material/lightbulb:")
    for x in tasks:
        sub = bool(x.get("parentId"))
        with st.container(border=not sub):
            cols = st.columns([0.6, 7, 1.6, 1.4, 1.1] if not sub else [0.6, 0.6, 6.4, 1.6, 1.1], vertical_alignment="center")
            if sub:
                cols[0].write("")
                cols = cols[1:]
            if cols[0].checkbox("Done", key=f"done_{x['id']}", label_visibility="collapsed"):
                send("complete_task", f"Done: {x['title']}. Nice.", id=x["id"])
                time.sleep(1)
                st.rerun()
            dots = " ".join("●" for _ in range(min(x.get("pomodoros") or 0, 8)))
            cols[1].markdown(f"{x['title']}" + (f"  \n<span class='dg-dots'>{dots}</span>" if dots else ""), unsafe_allow_html=True)
            if cols[2].button("Focus", key=f"f_{x['id']}", icon=":material/play_arrow:", width="stretch",
                              help="Start a 25-minute focus session on this task"):
                send("start_focus", f"Focusing on “{x['title']}” in Chrome.", minutes=25, task=x["title"])
            if not sub:
                if cols[3].button("Break down", key=f"b_{x['id']}", type="tertiary", width="stretch"):
                    breakdown_dialog(x["title"])
            else:
                cols[3].write("")

    done = [x for x in state.get("tasks") or [] if x.get("done")]
    if done:
        with st.expander(f"Done recently ({len(done)})"):
            for x in done[-20:][::-1]:
                st.markdown(f"~~{x['title']}~~")


@st.dialog("Break it down", width="medium")
def breakdown_dialog(task: str):
    st.caption(task)
    try:
        r = api.call("POST", "/api/breakdown", {"task": task}, timeout=40)
    except api.AgentDown:
        st.error("Couldn't reach the FocusGuard AI app.")
        return
    steps = r.get("steps") or []
    edited = [st.text_input(f"Step {i}", value=s, key=f"step_{i}") for i, s in enumerate(steps, 1)]
    if r.get("source") == "template":
        st.caption("Starter steps. Edit them to fit, or turn on Ollama in data/config.json for steps written by your local AI model.")
    else:
        st.caption("Written by the AI model on your computer.")
    if st.button("Add these steps", type="primary", width="stretch"):
        for s in edited:
            if s.strip():
                send("add_task", "Steps added under your task.", title=s.strip(), parent=task)
        st.toast("Steps added under your task.", icon=":material/check_circle:")
        refresh()
        time.sleep(1)
        st.rerun()


def page_coach():
    try:
        load_state()
    except api.AgentDown:
        agent_down()
        return
    hero([("Your coach", None)])
    st.markdown('<div class="dg-sub">Built from your own data, your goals file and a library of proven techniques. '
                'Nothing leaves this computer.</div>', unsafe_allow_html=True)

    left, right = st.columns([6, 5], gap="large")
    with left:
        stamp = st.session_state.get("coach_stamp", 0)
        try:
            r = load_coach(stamp)
        except api.AgentDown:
            st.error("Couldn't reach the FocusGuard AI app.")
            return
        with st.container(border=True):
            top = st.columns([5, 1], vertical_alignment="center")
            top[0].subheader("This week")
            if top[1].button("", icon=":material/refresh:", help="Refresh", type="tertiary"):
                st.session_state["coach_stamp"] = stamp + 1
                st.rerun()
            st.markdown(f"**{r.get('headline', '')}**")
            for i in r.get("insights", []):
                st.markdown(f"- {i}")
            if r.get("suggestion"):
                st.success(r["suggestion"], icon=":material/tips_and_updates:")
            if r.get("goal"):
                st.markdown(f'<div class="dg-quote">From your goals: “{r["goal"]["text"]}”</div>', unsafe_allow_html=True)
            if r.get("technique"):
                tq = r["technique"]
                st.markdown(f"#### Technique to try: {tq['title']}")
                st.write(tq["how"])
                if tq.get("try"):
                    st.caption(tq["try"])
                st.caption(f"From {tq['source']}")
            st.caption("Written by the AI model on your computer." if r.get("source") == "ollama"
                       else "Based on your data. Turn on Ollama in data/config.json for more personal wording.")

        prof = (load_state().get("profile") or {})
        if prof.get("completedAt"):
            with st.container(border=True):
                st.subheader("What the coach knows about you")
                trig = {"bored": "boredom", "avoiding": "avoiding something hard", "stress": "stress or anxiety",
                        "tired": "being tired", "habit": "pure habit", "fomo": "not wanting to miss out"}
                times = {"morning": "mornings", "afternoon": "afternoons", "evening": "evenings", "late": "late at night",
                         "bed": "in bed", "work": "while working or studying"}
                rows = []
                if prof.get("mainGoal"):
                    rows.append(f"**Main goal:** {prof['mainGoal']}")
                if prof.get("triggers"):
                    rows.append("**What pulls you in:** " + ", ".join(trig.get(t, t) for t in prof["triggers"]))
                if prof.get("times"):
                    rows.append("**Risky moments:** " + ", ".join(times.get(t, t) for t in prof["times"]))
                pr = prof.get("protect") or {}
                if pr.get("enabled"):
                    rows.append(f"**Protected hours:** {pr.get('start')} to {pr.get('end')}")
                st.markdown("  \n".join(rows) or "Nothing yet.")
                st.caption("From your setup answers. Change them in the extension: Settings → About you → Retake the questions.")

        with st.expander("Your goals (the coach reads this)", icon=":material/flag:"):
            text = st.text_area("goals.md", value=api.read_goals(), height=320, label_visibility="collapsed")
            if st.button("Save goals", type="primary"):
                api.write_goals(text)
                st.session_state["coach_stamp"] = stamp + 1
                st.toast("Saved. The coach will use it from now on.", icon=":material/check_circle:")

    with right:
        with st.container(border=True):
            st.subheader("Want to talk it through?")
            st.write("Chat with your agent on the **Agent** page. It can answer questions about your habits and take actions for you, "
                     "like planning your evening or starting a focus session.")
            st.page_link(pages[1], label="Open the Agent", icon=":material/smart_toy:")


def chat():
    with st.container(border=True):
        st.subheader("Ask your coach")
        history = st.session_state.setdefault("chat", [])
        suggestions = ["When do I scroll the most?", "What should I change this week?", "How do I stop opening Shorts at night?"]
        picked = None
        if not history:
            picked = st.pills("Try asking", suggestions, key="chat_pill", label_visibility="collapsed")
        box = st.container(height=420)
        with box:
            for m in history:
                with st.chat_message(m["role"], avatar=":material/person:" if m["role"] == "user" else ":material/support:"):
                    st.markdown(m["text"])
                    if m.get("sources"):
                        st.caption("Sources: " + ", ".join(s["title"] for s in m["sources"]))
        q = st.chat_input("Ask about your habits, your week or your goals") or picked
        if q:
            history.append({"role": "user", "text": q})
            try:
                with st.spinner("Thinking…"):
                    r = api.call("POST", "/api/ask", {"question": q}, timeout=60)
                history.append({"role": "assistant", "text": r.get("answer", ""), "sources": r.get("sources", [])})
            except api.AgentDown:
                history.append({"role": "assistant", "text": "I couldn't reach the FocusGuard AI app. Is it running?"})
            st.rerun()


def page_agent():
    data = get_data()
    if not data:
        return
    state, status = data
    hero([("Your agent", None)])
    st.markdown('<div class="dg-sub">It plans your day, steps in when you keep reaching for blocked sites, and takes actions when you ask. '
                'It can only make blocking stricter, only until midnight, and never starts a session without your click.</div>',
                unsafe_allow_html=True)
    agent_pipeline(state, status)
    left, right = st.columns([6, 5], gap="large")
    with left:
        plan_card()
        activity_log()
    with right:
        agent_chat()


def agent_pipeline(state: dict, status: dict):
    """Make the agent loop visible: observe → understand → decide → act."""
    today = status.get("today") or {}
    current = status.get("current") or {}
    drift = status.get("drift") or {}
    control = status.get("control") or {}
    synced = state.get("updated_at")
    age = round((time.time() * 1000 - synced) / 60000) if synced else None
    last = (status.get("alerts") or [None])[0]
    action = "Watching"
    if control.get("rescue_until"):
        action = "Focus Rescue active"
    elif control.get("paused_until"):
        action = "Paused"
    elif last and not last.get("outcome"):
        action = "Waiting for your response"
    cards = [
        ("1 · Observe", "Chrome", f"Synced {age} min ago" if age is not None else "Waiting for Chrome"),
        ("2 · Understand", "Drift level", f"{drift.get('score', 0)}/100 · {drift.get('label', 'intentional')}"),
        ("3 · Decide", "Today", f"{today.get('nudges', 0)} nudges · {today.get('exits', 0)} exits"),
        ("4 · Act", action, "Blocking, focus, or intervention"),
    ]
    cols = st.columns(4, gap="small")
    for col, (heading, value, detail) in zip(cols, cards):
        with col:
            with st.container(border=True):
                st.caption(heading)
                st.markdown(f"**{value}**")
                st.caption(detail)


def page_connect():
    """The first-run control center for connecting the local Chrome extension."""
    hero([("Connect your browser", None)])
    st.markdown('<div class="dg-sub">The Streamlit app is your control center. The Chrome extension observes coarse activity, shows interventions, and applies focus actions.</div>', unsafe_allow_html=True)
    try:
        state = load_state()
        status = load_status()
        connected = bool(state.get("updated_at")) and bool(status.get("now"))
    except api.AgentDown:
        state, status, connected = {}, {}, False

    left, right = st.columns([6, 5], gap="large")
    with left:
        with st.container(border=True):
            st.subheader("Add FocusGuard AI to Chrome")
            st.write("Chrome does not allow a website to silently install an extension. Download the local extension, then add it once through Chrome's Developer mode.")
            st.download_button("Download extension", data=extension_bundle(), file_name="focusguard-ai-chrome-extension.zip",
                               mime="application/zip", type="primary", width="stretch", icon=":material/download:")
            st.markdown("**Install it:**")
            st.markdown("1. Unzip the downloaded file.\n2. Open `chrome://extensions`.\n3. Turn on **Developer mode**.\n4. Click **Load unpacked**.\n5. Select the unzipped folder containing `manifest.json`.")
            st.code("chrome://extensions", language="text")
    with right:
        with st.container(border=True):
            st.subheader("Connection status")
            if connected:
                st.success("Chrome extension connected", icon=":material/check_circle:")
                st.caption("The local agent is receiving browser signals and can send interventions.")
                st.metric("Tracked openings today", (status.get("today") or {}).get("opens", 0))
            else:
                st.warning("Waiting for the extension", icon=":material/hourglass_top:")
                st.caption("Start the agent, load the extension, and open its popup. This page will update when Chrome syncs.")
            if st.button("Refresh connection", icon=":material/refresh:"):
                refresh(); st.rerun()

    with st.container(border=True):
        st.subheader("How the system works")
        st.markdown("**Chrome extension → Local agent → Drift analysis → Intervention → Report**")
        st.caption("The extension sends only coarse labels and counters such as site category, scrolling, and session duration. URLs, titles, searches, and video identifiers stay out of the agent database.")


def plan_card():
    try:
        plan = api.call("GET", "/api/plan")
    except api.AgentDown:
        return
    today = dt.date.today().isoformat()
    with st.container(border=True):
        top = st.columns([4, 2], vertical_alignment="center")
        top[0].subheader("Today's plan")
        has = plan.get("date") == today and not plan.get("pending")
        if top[1].button("Re-plan my day" if has else "Plan my day", type="secondary" if has else "primary",
                         icon=":material/auto_awesome:", width="stretch"):
            with st.spinner("Your agent is planning…"):
                try:
                    api.call("POST", "/api/plan", {}, timeout=120)
                except api.AgentDown:
                    st.error("Couldn't reach the FocusGuard AI app.")
            st.rerun()
        if not has:
            st.caption("No plan yet today. Your agent makes one automatically the first time you're at the computer each day.")
            return
        st.markdown(f"**{plan.get('message', '')}**")
        if plan.get("first_task"):
            st.markdown(f"Start with: **{plan['first_task']}**")
        blocks = plan.get("focus_blocks") or []
        if blocks:
            st.markdown("##### Focus sessions")
            status_badge = {"planned": ("planned", "blue"), "prompted": ("reminded", "orange"), "started": ("started", "green"),
                            "skipped": ("skipped", "gray"), "missed": ("missed", "gray")}
            for b in blocks:
                with st.container(horizontal=True, vertical_alignment="center"):
                    hh, mm = map(int, b["time"].split(":"))
                    when = dt.time(hh, mm).strftime("%I:%M %p").lstrip("0")
                    st.markdown(f"**{when}** · {b['minutes']} min" + (f" · {b['task']}" if b.get("task") else ""))
                    text, color = status_badge.get(b.get("status"), ("planned", "blue"))
                    st.badge(text, color=color)
                    if b.get("status") in ("planned", "prompted", "missed") and st.button("Start now", key=f"blk_{b['id']}", type="tertiary"):
                        send("start_focus", "Starting your focus session in Chrome.", minutes=b["minutes"], task=b.get("task"))
        changes = plan.get("site_changes") or []
        if changes:
            st.markdown("##### Tightened for today")
            for c in changes:
                what = f"limited to {c['limit_minutes']} min" if c["to"] == "limit" else "blocked"
                st.markdown(f"- **{c['label']}** {what} until midnight" + (f" — {c['reason']}" if c.get("reason") else ""))
        st.caption("Planned by the AI model on your computer." if plan.get("source") == "ollama"
                   else "Planned with rules. Turn on Ollama in data/config.json and the AI model plans for you.")


def activity_log():
    try:
        log = api.call("GET", "/api/agent/log").get("log", [])
    except api.AgentDown:
        return
    with st.container(border=True):
        st.subheader("What your agent did")
        if not log:
            st.caption("Nothing yet.")
            return
        labels = {"plan": ("planned", "blue"), "intervene": ("stepped in", "orange"), "chat": ("chat", "violet"),
                  "prompt": ("reminded", "gray"), "revert": ("undid limits", "gray")}
        for e in log[:12]:
            when = time.strftime("%a %I:%M %p", time.localtime(e["ts"] / 1000)).replace(" 0", " ")
            text, color = labels.get(e["kind"], (e["kind"], "gray"))
            with st.container(horizontal=True, vertical_alignment="top"):
                st.badge(text, color=color)
                body = f"<span class='dg-muted'>{when}</span>  \n{e.get('message') or ''}"
                if e.get("actions"):
                    body += "  \n" + "  \n".join(f"→ {a}" for a in e["actions"])
                st.markdown(body, unsafe_allow_html=True)


def agent_chat():
    with st.container(border=True):
        st.subheader("Talk to your agent")
        history = st.session_state.setdefault("agent_chat", [])
        suggestions = ["Plan my evening", "I can't focus, help me start", "When do I scroll the most?", "Limit Reddit to 15 minutes today"]
        picked = None
        if not history:
            picked = st.pills("Try", suggestions, key="agent_pill", label_visibility="collapsed")
        box = st.container(height=440)
        with box:
            for m in history:
                with st.chat_message(m["role"], avatar=":material/person:" if m["role"] == "user" else ":material/smart_toy:"):
                    st.markdown(m["text"])
                    for a in m.get("actions") or []:
                        st.caption(f"✓ {a}")
                    if m.get("sources"):
                        st.caption("Sources: " + ", ".join(s["title"] for s in m["sources"]))
        q = st.chat_input("Ask, or tell your agent what you need") or picked
        if q:
            history.append({"role": "user", "text": q})
            try:
                with st.spinner("Thinking…"):
                    r = api.call("POST", "/api/agent/chat",
                                 {"message": q, "history": [{"role": m["role"], "content": m["text"]} for m in history[:-1]]}, timeout=120)
                history.append({"role": "assistant", "text": r.get("answer", ""), "actions": r.get("actions", []),
                                "sources": r.get("sources", [])})
                if r.get("actions"):
                    refresh()
            except api.AgentDown:
                history.append({"role": "assistant", "text": "I couldn't reach the FocusGuard AI app. Is it running?"})
            st.rerun()


MODE_LABEL = {"off": "Off", "track": "Track", "limit": "Limit", "block": "Block"}


def page_sites():
    data = get_data()
    if not data:
        return
    state, _ = data
    hero([("Your sites", None)])
    st.markdown('<div class="dg-sub"><b>Track</b> counts time. <b>Limit</b> allows a set number of minutes a day, then blocks. '
                '<b>Block</b> sends you to your Focus page. During a focus session, everything that isn\'t Off is blocked.</div>',
                unsafe_allow_html=True)
    rules = state.get("rules") or []
    df = pd.DataFrame([{"id": r["id"], "Site": r["label"], "Mode": MODE_LABEL[r["mode"]], "Daily limit (min)": r.get("limitMin", 15)}
                       for r in rules])
    edited = st.data_editor(
        df, hide_index=True, width="stretch", disabled=["Site"], column_order=["Site", "Mode", "Daily limit (min)"],
        column_config={
            "Mode": st.column_config.SelectboxColumn("Mode", options=list(MODE_LABEL.values()), required=True),
            "Daily limit (min)": st.column_config.NumberColumn("Daily limit (min)", min_value=1, max_value=600, step=5,
                                                              help="Only used when Mode is Limit."),
        }, key="sites_editor")
    changed = []
    for (_, a), (_, b) in zip(df.iterrows(), edited.iterrows()):
        if a["Mode"] != b["Mode"] or (b["Mode"] == "Limit" and a["Daily limit (min)"] != b["Daily limit (min)"]):
            changed.append(b)
    if changed:
        if st.button(f"Apply {api.plural(len(changed), 'change')}", type="primary", icon=":material/check:"):
            for b in changed:
                mode = {v: k for k, v in MODE_LABEL.items()}[b["Mode"]]
                send("set_site_mode", f"{b['Site']}: {b['Mode']}", site=b["id"], mode=mode, limit_minutes=int(b["Daily limit (min)"]))
            st.toast("Sent to Chrome. Changes apply within a few seconds.", icon=":material/check_circle:")
            refresh()
            time.sleep(1.5)
            st.rerun()

    st.subheader("Add a website")
    with st.form("add_site", clear_on_submit=True, border=False):
        c1, c2, c3 = st.columns([4, 2, 1], vertical_alignment="bottom")
        site = c1.text_input("Website", placeholder="netflix.com")
        mode = c2.selectbox("Mode", ["Block", "Limit", "Track"])
        if c3.form_submit_button("Add", type="primary", width="stretch") and site.strip():
            send("set_site_mode", f"Added {site.strip()}.", site=site.strip(), mode=mode.lower(), limit_minutes=15)
            time.sleep(1.5)
            st.rerun()


# ---------------------------------------------------------------------------
# Shell

def sidebar():
    with st.sidebar:
        st.caption("Status")
        try:
            status = load_status()
            state = load_state()
        except api.AgentDown:
            st.caption(":red[●] App not running")
            return
        ctl = status.get("control", {})
        age = (time.time() * 1000 - (state.get("updated_at") or 0)) / 60000 if state else 99
        if ctl.get("paused_until"):
            st.caption(f":orange[●] Paused until {time.strftime('%I:%M %p', time.localtime(ctl['paused_until'] / 1000)).lstrip('0')}")
            if st.button("Resume", width="stretch"):
                api.call("POST", "/api/pause", {"minutes": 0}); refresh(); st.rerun()
        else:
            st.caption(":green[●] Watching" + ("" if age < 3 else f" · Chrome last synced {round(age)} min ago"))
            if st.button("Pause 1 hour", width="stretch", icon=":material/pause:"):
                api.call("POST", "/api/pause", {"minutes": 60}); refresh(); st.rerun()


pages = [
    st.Page(page_today, title="Today", icon=":material/today:", default=True),
    st.Page(page_connect, title="Connect", icon=":material/extension:", url_path="connect"),
    st.Page(page_agent, title="Agent", icon=":material/smart_toy:", url_path="agent"),
    st.Page(page_report, title="Report", icon=":material/insights:", url_path="report"),
    st.Page(page_tasks, title="Tasks", icon=":material/checklist:", url_path="tasks"),
    st.Page(page_coach, title="Coach", icon=":material/support:", url_path="coach"),
    st.Page(page_sites, title="Sites", icon=":material/block:", url_path="sites"),
]
st.logo(str(api.ROOT / "extension" / "icons" / "icon128.png"), size="large")
with st.sidebar:
    st.markdown('<div class="dg-sidebar-brand">FocusGuard AI</div>', unsafe_allow_html=True)
    st.page_link(pages[1], label="Connect Chrome extension", icon=":material/extension:")
    st.download_button(
        "Download extension",
        data=extension_bundle(),
        file_name="focusguard-ai-chrome-extension.zip",
        mime="application/zip",
        width="stretch",
        icon=":material/download:",
        help="Unzip it, then load the extension folder at chrome://extensions.",
    )
    with st.expander("Install the extension", expanded=False):
        st.markdown(
            "1. Download and unzip the file.\n"
            "2. Open `chrome://extensions`.\n"
            "3. Turn on **Developer mode**.\n"
            "4. Click **Load unpacked**.\n"
            "5. Select the unzipped **extension** folder."
        )
    with st.expander("Why FocusGuard AI is different", expanded=False):
        st.markdown(
            "FocusGuard AI does not only pause an app before opening. It recognizes "
            "reopening, app-hopping, endless scrolling, risky hours, and whether "
            "your current behavior matches your real goal. It then offers a "
            "specific exit path: a task, focus session, reset, or recovery plan."
        )
nav = st.navigation(pages)
sidebar()
nav.run()
