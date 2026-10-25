"""Public, privacy-safe FocusGuard AI showcase.

This page intentionally uses synthetic data. It never imports the local agent,
reads the local SQLite database, or exposes the browser extension token.
"""
import streamlit as st
import pandas as pd

st.set_page_config(page_title="FocusGuard AI Demo", page_icon="🛡️", layout="wide")

st.markdown("""
<style>
.block-container {max-width: 1120px; padding-top: 2rem;}
.hero {padding: 1.8rem 2rem; border-radius: 20px; background: linear-gradient(135deg,#173f37,#2b7563); color:white; margin-bottom:1.2rem;}
.hero h1 {margin:0 0 .4rem; font-size:2.5rem;}
.muted {opacity:.78;}
.trace {padding:.7rem 1rem; margin:.35rem 0; border-left:4px solid #36b58c; background:#f1f8f5; border-radius:7px;}
</style>
""", unsafe_allow_html=True)

st.markdown("""
<div class="hero">
<h1>🛡️ FocusGuard AI</h1>
<div>Privacy-first autonomous agent for reducing doom scrolling</div>
<div class="muted" style="margin-top:.7rem">Public product demonstration using synthetic browsing data</div>
</div>
""", unsafe_allow_html=True)

st.info("This is a safe showcase version. It does not connect to a visitor's browser, personal data, or your private Mac agent.")

overview, trace, architecture = st.tabs(["Live demo", "Agent trace", "How it works"])

with overview:
    st.subheader("A simulated user is drifting")
    a, b, c, d = st.columns(4)
    a.metric("Drift score", "82 / 100", "deep loop")
    b.metric("Short videos", "47", "in 14 minutes")
    c.metric("Quick returns", "4", "in 30 minutes")
    d.metric("Focus rescued", "18 min", "today")

    st.subheader("What the agent observed")
    data = pd.DataFrame([
        {"Signal": "Short-video pace", "Value": "3.4 videos/min", "Meaning": "Fast, automatic swiping"},
        {"Signal": "Feed scrolling", "Value": "9,200 px/min", "Meaning": "Continuous feed movement"},
        {"Signal": "Goal mismatch", "Value": "Study goal + endless feed", "Meaning": "Current activity conflicts with intent"},
        {"Signal": "Recent returns", "Value": "4", "Meaning": "Repeated reopening pattern"},
    ])
    st.dataframe(data, use_container_width=True, hide_index=True)

    st.subheader("Recommended action")
    st.success("Start a 10-minute Focus Rescue, keep distracting sites closed, and begin the next task: Prepare ML assignment.")
    if st.button("Simulate accepting Focus Rescue", type="primary"):
        st.toast("Focus Rescue started — simulated demo action", icon="✅")

with trace:
    st.subheader("Observe → Understand → Decide → Act → Learn")
    for item in [
        "Observed coarse browser signals through the Chrome extension",
        "Retrieved the user's goal and a relevant anti-scrolling technique with RAG",
        "Calculated a transparent drift score using deterministic rules",
        "Selected an intervention using recent outcomes and safety limits",
        "Queued Focus Rescue for the browser extension after user approval",
        "Recorded the outcome to improve future intervention timing",
    ]:
        st.markdown(f'<div class="trace">✓ {item}</div>', unsafe_allow_html=True)

with architecture:
    st.subheader("Production architecture")
    st.code("""Chrome extension  →  localhost agent  →  SQLite + RAG
       observe                 understand
                                      ↓
                         planner + guarded tools
                                      ↓
                   nudge / focus rescue / site control

Optional local reasoning: Ollama
Optional MCP access: Claude Desktop / compatible MCP client
Public demo: synthetic data only""", language="text")
    st.markdown("""
    **Privacy boundary:** the public demo is separate from the personal agent.
    Your real browser events, goals, token, and local database never belong in a
    public repository or deployment.
    """)
