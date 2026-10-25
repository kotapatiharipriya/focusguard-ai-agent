"""Public FocusGuard AI showcase with the same product areas as the local app.

This deployment uses synthetic data only. It never connects to a browser,
personal SQLite database, local token, or private RAG notes.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from src.agent import run_demo

st.set_page_config(page_title="FocusGuard AI", page_icon="🛡️", layout="wide", initial_sidebar_state="expanded")

st.markdown("""
<style>
.block-container {max-width: 1200px; padding-top: 1.8rem;}
.brand {font-size:1.35rem;font-weight:750;letter-spacing:-.02em;margin-bottom:1.2rem;}
.hero {padding:1.5rem 1.7rem;border-radius:18px;background:linear-gradient(135deg,#163d36,#2b7563);color:#fff;margin-bottom:1.2rem;}
.hero h1 {margin:0 0 .35rem;font-size:2.25rem;}
.hero p {margin:0;opacity:.84;}
.trace {padding:.68rem 1rem;margin:.35rem 0;border-left:4px solid #31ad86;background:#f1f8f5;border-radius:7px;}
.privacy {padding:.65rem .85rem;border-radius:8px;background:#fff6d9;color:#5b4810;font-size:.9rem;}
.small {opacity:.7;font-size:.9rem;}
</style>
""", unsafe_allow_html=True)

if "tasks" not in st.session_state:
    st.session_state.tasks = [
        {"title": "Prepare ML assignment", "done": False},
        {"title": "Review agent architecture", "done": False},
        {"title": "Apply to one internship", "done": True},
    ]

demo = run_demo()
signals = demo["signals"]
score = demo["score"]

with st.sidebar:
    st.markdown('<div class="brand">🛡️ FocusGuard AI</div>', unsafe_allow_html=True)
    st.caption("Privacy-first autonomous focus agent")
    page = st.radio("Navigate", ["Today", "Agent", "Report", "Tasks", "Coach", "Sites", "Connect"], label_visibility="collapsed")
    st.divider()
    st.markdown('<div class="privacy">Public showcase mode<br><span class="small">Synthetic data only · no browser connection</span></div>', unsafe_allow_html=True)
    st.divider()
    st.caption("Local product: Chrome extension + Python agent + SQLite + RAG + MCP + Ollama")

st.markdown('<div class="hero"><h1>FocusGuard AI</h1><p>Notice the loop. Protect your attention. Return to what matters.</p></div>', unsafe_allow_html=True)

if page == "Today":
    st.subheader("Today")
    st.caption("A simulated user session demonstrating the autonomous agent.")
    a, b, c, d = st.columns(4)
    a.metric("Drift level", f"{score['score']} / 100", score["label"])
    b.metric("Distracting time", "1 h 12 min", "+18% vs usual")
    c.metric("Short videos", str(signals["short_videos"]), "in 14 minutes")
    d.metric("Focus rescued", "18 min", "2 sessions")
    st.divider()
    left, right = st.columns([1.15, .85])
    with left:
        st.subheader("What the agent noticed")
        st.dataframe(pd.DataFrame([
            {"Signal": "Short-video pace", "Value": "3.4/min", "Why it matters": "Fast automatic swiping"},
            {"Signal": "Feed movement", "Value": "9,200 px/min", "Why it matters": "Continuous scrolling"},
            {"Signal": "Quick returns", "Value": "4", "Why it matters": "Repeated reopening"},
            {"Signal": "Goal mismatch", "Value": "Detected", "Why it matters": "Study goal vs endless feed"},
        ]), use_container_width=True, hide_index=True)
    with right:
        st.subheader("Recommended next step")
        st.success(demo["decision"]["message"])
        if st.button("Start Focus Rescue", type="primary", use_container_width=True):
            st.toast("Focus Rescue started — simulated action", icon="✅")
        st.caption("In the private version, this action is sent to the Chrome extension after approval.")
    st.subheader("Today at a glance")
    st.bar_chart(pd.DataFrame({"Distracting minutes": [4, 8, 5, 12, 18, 14, 11]}, index=["8 AM", "10 AM", "12 PM", "2 PM", "4 PM", "6 PM", "8 PM"]))

elif page == "Agent":
    st.subheader("Autonomous Agent")
    st.caption("Observe → Understand → Decide → Act → Learn")
    st.success("Agent status: running in showcase mode")
    for item in demo["trace"]:
        st.markdown(f'<div class="trace">✓ {item}</div>', unsafe_allow_html=True)
    st.subheader("Agent decision")
    st.json({"drift_score": score, "retrieved_technique": demo["technique"], "decision": demo["decision"]})
    st.subheader("Guarded tools available in the private agent")
    st.write("`start_focus` · `set_site_mode` · `add_task` · `complete_task` · `pause` · `search_notes`")
    st.caption("Every action is bounded by deterministic safety rules and recorded in the audit log.")

elif page == "Report":
    st.subheader("Report")
    st.caption("Synthetic seven-day report")
    a, b, c = st.columns(3)
    a.metric("Distracting time", "8 h 42 min", "−21% vs previous week")
    b.metric("Focus time", "6 h 15 min", "+34% vs previous week")
    c.metric("Turned around", "19 times", "12 rescues completed")
    daily = pd.DataFrame({"Distracting": [92, 78, 65, 84, 51, 43, 37], "Focused": [22, 35, 41, 28, 55, 60, 64]}, index=["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"])
    st.line_chart(daily)
    st.subheader("Most common drift signals")
    st.table(pd.DataFrame([
        {"Pattern": "Fast short-video swiping", "Occurrences": 11, "Agent response": "Focus Rescue"},
        {"Pattern": "Late-night feed scrolling", "Occurrences": 7, "Agent response": "Protected hours"},
        {"Pattern": "Repeated reopening", "Occurrences": 5, "Agent response": "Stronger nudge"},
    ]))

elif page == "Tasks":
    st.subheader("Tasks")
    st.caption("The agent uses your tasks to suggest a concrete alternative to scrolling.")
    for i, task in enumerate(st.session_state.tasks):
        st.session_state.tasks[i]["done"] = st.checkbox(task["title"], value=task["done"], key=f"task_{i}")
    st.divider()
    new_task = st.text_input("Add a task", placeholder="e.g. Read two pages of my ML paper")
    if st.button("Add task") and new_task.strip():
        st.session_state.tasks.append({"title": new_task.strip(), "done": False})
        st.rerun()

elif page == "Coach":
    st.subheader("RAG Coach")
    st.caption("The public demo retrieves from a small synthetic knowledge base. The private version searches your goals, techniques, and weekly history.")
    st.text_input("Ask your coach", "How do I exit this scrolling loop?")
    if st.button("Ask coach", type="primary"):
        st.success("Try a stopping cue: put the device down, name the next task, and start for two minutes.")
        st.caption(f"Retrieved source: {demo['technique']['title']} · local RAG")
    st.subheader("Retrieved technique")
    st.info(demo["technique"]["text"])

elif page == "Sites":
    st.subheader("Sites")
    st.caption("Synthetic settings preview")
    st.dataframe(pd.DataFrame([
        {"Site": "YouTube Shorts", "Mode": "Block", "Daily limit": "—", "Reason": "Fast-feed loop"},
        {"Site": "Instagram Reels", "Mode": "Limit", "Daily limit": "15 min", "Reason": "Intentional check-ins"},
        {"Site": "Reddit", "Mode": "Track", "Daily limit": "—", "Reason": "Observe first"},
    ]), use_container_width=True, hide_index=True)
    st.caption("In the private version, site changes are sent to the Chrome extension through the local agent.")

elif page == "Connect":
    st.subheader("Connect Chrome extension")
    st.info("The public showcase does not connect to a visitor’s browser. The private version pairs a locally loaded Chrome extension with the local FocusGuard agent on port 8765.")
    st.markdown("""
    **Private setup:**

    1. Start the local Python agent.
    2. Load `extension/` from `chrome://extensions`.
    3. Pair the extension with the local agent.
    4. Use this dashboard locally at `localhost:8501`.
    """)
    st.code("Chrome extension → localhost:8765 agent → SQLite/RAG → guarded actions", language="text")

st.divider()
st.caption("FocusGuard AI public showcase · synthetic data only · full private implementation available in the repository")
