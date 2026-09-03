"""
Structured JSONL logging. Every graph node writes one line per event.
The Streamlit dashboard tails these files to render live progress.
Using JSONL (not a single JSON array) means the dashboard can read a
run that is still in progress without waiting for it to finish.
"""

import json
import os
import time
from pathlib import Path

LOG_DIR = Path(os.getenv("LOG_DIR", "logs"))
LOG_DIR.mkdir(exist_ok=True)


class RunLogger:
    def __init__(self, run_id: str | None = None):
        self.run_id = run_id or time.strftime("%Y%m%d_%H%M%S")
        self.path = LOG_DIR / f"run_{self.run_id}.jsonl"

    def log(self, event_type: str, **fields):
        record = {
            "timestamp": time.time(),
            "event": event_type,
            **fields,
        }
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, default=str) + "\n")
        return record

    def log_objective(self, objective: str, repo_path: str, max_iterations: int):
        return self.log("run_started", objective=objective, repo_path=repo_path, max_iterations=max_iterations)

    def log_plan(self, iteration: int, plan_text: str):
        return self.log("plan", iteration=iteration, plan=plan_text)

    def log_proposed_action(self, iteration: int, action: dict):
        return self.log("proposed_action", iteration=iteration, action=action)

    def log_static_check(self, iteration: int, result: dict):
        return self.log("static_check", iteration=iteration, result=result)

    def log_semantic_check(self, iteration: int, result: dict):
        return self.log("semantic_check", iteration=iteration, result=result)

    def log_execution(self, iteration: int, result: dict):
        return self.log("execution", iteration=iteration, result=result)

    def log_rejection(self, iteration: int, checker: str, reason: str):
        return self.log("rejected", iteration=iteration, checker=checker, reason=reason)

    def log_run_finished(self, summary: str, total_iterations: int):
        return self.log("run_finished", summary=summary, total_iterations=total_iterations)
