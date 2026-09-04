"""
Streamlit dashboard: live view into every action the agent proposed,
whether it was allowed or blocked by each guardrail layer, and why.

Run with:
    streamlit run src/dashboard/app.py
"""

import json
import time
from pathlib import Path

import pandas as pd
import streamlit as st

st.set_page_config(page_title="OpenClaw — Guardrail Monitor", layout="wide")

LOG_DIR = Path("logs")


def load_runs():
    if not LOG_DIR.exists():
        return []
    return sorted(LOG_DIR.glob("run_*.jsonl"), reverse=True)


def load_events(path: Path):
    events = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return events


st.title("🛡️ OpenClaw — Agent Guardrail Monitor")
st.caption("Live view of every action the agent proposed and whether the guardrail layer allowed or blocked it.")

evaluation_summary_path = Path("evaluation/results/summary.json")
if evaluation_summary_path.exists():
    try:
        evaluation_summary = json.loads(evaluation_summary_path.read_text(encoding="utf-8"))
        st.header("Evaluation results")
        by_model = evaluation_summary.get("by_model", {})
        if by_model:
            evaluation_rows = [
                {"model": model, "success_rate": values.get("success_rate", 0),
                 "average_seconds": values.get("average_execution_seconds", 0),
                 "average_iterations": values.get("average_iterations", 0)}
                for model, values in by_model.items()
            ]
            evaluation_df = pd.DataFrame(evaluation_rows).set_index("model")
            e1, e2, e3 = st.columns(3)
            e1.metric("Tasks evaluated", evaluation_summary.get("overall", {}).get("tasks", 0))
            e2.metric("Overall success rate", f"{evaluation_summary.get('overall', {}).get('success_rate', 0)}%")
            e3.metric("Blocked actions", evaluation_summary.get("overall", {}).get("blocked_actions", 0))
            st.caption("Success rate by coder model")
            st.bar_chart(evaluation_df["success_rate"])
            st.caption("Average execution time (seconds) by coder model")
            st.bar_chart(evaluation_df["average_seconds"])
    except (OSError, json.JSONDecodeError, ValueError) as error:
        st.warning(f"Could not load evaluation summary: {error}")

runs = load_runs()
if not runs:
    st.info("No runs yet. Start one with: `python main.py --objective \"...\" --repo ...`")
    st.stop()

run_labels = [p.stem.replace("run_", "") for p in runs]
selected = st.selectbox("Select a run", run_labels)
selected_path = LOG_DIR / f"run_{selected}.jsonl"

auto_refresh = st.checkbox("Auto-refresh (live mode)", value=True)

events = load_events(selected_path)

objective_event = next((e for e in events if e["event"] == "run_started"), None)
if objective_event:
    st.subheader("Objective")
    st.write(objective_event["objective"])
    st.caption(f"Repo: {objective_event['repo_path']} | Max iterations: {objective_event['max_iterations']}")

# --- Summary metrics ---
proposed = [e for e in events if e["event"] == "proposed_action"]
rejected = [e for e in events if e["event"] == "rejected"]
executed = [e for e in events if e["event"] == "execution"]
finished = next((e for e in events if e["event"] == "run_finished"), None)

col1, col2, col3, col4 = st.columns(4)
col1.metric("Actions proposed", len(proposed))
col2.metric("Executed", len(executed))
col3.metric("Blocked (static)", sum(1 for e in rejected if e["checker"] == "static"))
col4.metric("Blocked (semantic)", sum(1 for e in rejected if e["checker"] == "semantic"))

if finished:
    st.success(finished["summary"])

st.divider()
st.subheader("Timeline")

# Reconstruct a per-iteration table
by_iter = {}
for e in events:
    it = e.get("iteration")
    if it is None:
        continue
    by_iter.setdefault(it, {"iteration": it})
    if e["event"] == "plan":
        by_iter[it]["plan"] = e["plan"]
    elif e["event"] == "proposed_action":
        action = e["action"]
        by_iter[it]["action_type"] = action.get("type")
        by_iter[it]["action_content"] = action.get("content", "")[:200]
        by_iter[it]["rationale"] = action.get("rationale", "")
    elif e["event"] == "static_check":
        by_iter[it]["static_allowed"] = e["result"]["allowed"]
        by_iter[it]["static_reason"] = e["result"]["reason"]
    elif e["event"] == "semantic_check":
        by_iter[it]["semantic_allowed"] = e["result"]["allowed"]
        by_iter[it]["semantic_reason"] = e["result"]["reason"]
    elif e["event"] == "execution":
        by_iter[it]["exit_code"] = e["result"].get("exit_code")
        by_iter[it]["timed_out"] = e["result"].get("timed_out")
    elif e["event"] == "rejected":
        by_iter[it]["rejected_by"] = e["checker"]
        by_iter[it]["rejection_reason"] = e["reason"]

if by_iter:
    df = pd.DataFrame(list(by_iter.values())).sort_values("iteration")
    for _, row in df.iterrows():
        with st.expander(f"Iteration {int(row['iteration'])} — {row.get('action_type', '?')}"):
            st.markdown(f"**Plan:** {row.get('plan', '(n/a)')}")
            st.markdown(f"**Proposed action ({row.get('action_type', '?')}):**")
            st.code(row.get("action_content", ""), language="python" if row.get("action_type") == "python" else "bash")
            if row.get("rationale"):
                st.markdown(f"**Agent's stated rationale:** {row['rationale']}")

            c1, c2 = st.columns(2)
            with c1:
                if "static_allowed" in row and pd.notna(row.get("static_allowed")):
                    icon = "✅" if row["static_allowed"] else "🚫"
                    st.markdown(f"{icon} **Static check:** {row.get('static_reason', '')}")
            with c2:
                if "semantic_allowed" in row and pd.notna(row.get("semantic_allowed")):
                    icon = "✅" if row["semantic_allowed"] else "🚫"
                    st.markdown(f"{icon} **Semantic check:** {row.get('semantic_reason', '')}")

            if pd.notna(row.get("rejected_by")):
                st.error(f"BLOCKED by {row['rejected_by']} check: {row.get('rejection_reason', '')}")
            elif "exit_code" in row and pd.notna(row.get("exit_code")):
                if row.get("timed_out"):
                    st.warning("Execution timed out")
                elif row["exit_code"] == 0:
                    st.success(f"Executed successfully (exit code {int(row['exit_code'])})")
                else:
                    st.warning(f"Executed with non-zero exit code: {int(row['exit_code'])}")

st.divider()
with st.expander("Raw event log (JSONL)"):
    st.json(events)

if auto_refresh:
    time.sleep(2)
    st.rerun()
