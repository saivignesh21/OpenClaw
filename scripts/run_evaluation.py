"""Run coding tasks or labelled guardrail fixtures and write metrics."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.evaluation import summarize_guardrail_results, write_results
from src.guardrails import run_semantic_check, run_static_check


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate OpenClaw across tasks and model configurations")
    parser.add_argument("--tasks", default="evaluation/tasks.json")
    parser.add_argument("--models", nargs="+", default=["qwen2.5-coder:7b", "llama3.1:8b"])
    parser.add_argument("--planner-model", default=os.getenv("PLANNER_MODEL", "llama3.1:8b"))
    parser.add_argument("--max-iterations", type=int, default=4)
    parser.add_argument("--output", default="evaluation/results")
    parser.add_argument("--guardrail-only", action="store_true")
    parser.add_argument("--fresh", action="store_true", help="Ignore an existing task results.json checkpoint")
    args = parser.parse_args()
    root = ROOT
    output = root / args.output

    if args.guardrail_only:
        cases = json.loads((root / "evaluation/guardrail_cases.json").read_text(encoding="utf-8"))
        records = []
        for model in args.models:
            os.environ["PLANNER_MODEL"] = model
            for case in cases:
                static = run_static_check(case["action"])
                semantic = run_semantic_check(case["objective"], case["action"])
                records.append({"case_id": case["id"], "model": model,
                                "expected_allowed": case["expected_allowed"],
                                "static_allowed": static.allowed, "semantic_allowed": semantic.allowed})
        output.mkdir(parents=True, exist_ok=True)
        (output / "guardrail_results.json").write_text(json.dumps(records, indent=2), encoding="utf-8")
        summary = summarize_guardrail_results(records)
        (output / "guardrail_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(json.dumps(summary, indent=2))
        return 0

    tasks = json.loads((root / args.tasks).read_text(encoding="utf-8"))
    checkpoint = output / "results.json"
    records = [] if args.fresh or not checkpoint.exists() else json.loads(checkpoint.read_text(encoding="utf-8"))
    completed_keys = {(row.get("model"), row.get("task_id")) for row in records}
    for model in args.models:
        for task in tasks:
            if (model, task["id"]) in completed_keys:
                print(f"{model} | {task['id']} | resumed from checkpoint")
                continue
            workspace = root / "evaluation" / "workspaces" / f"{model.replace(':', '_')}_{task['id']}"
            if workspace.exists():
                shutil.rmtree(workspace)
            shutil.copytree(root / task["repo"], workspace)
            env = os.environ.copy()
            env.update({"CODER_MODEL": model, "PLANNER_MODEL": args.planner_model})
            run_log_dir = output / "logs" / f"{model.replace(':', '_')}_{task['id']}"
            run_log_dir.mkdir(parents=True, exist_ok=True)
            env["LOG_DIR"] = str(run_log_dir)
            command = [sys.executable, "main.py", "--objective", task["objective"], "--repo", str(workspace), "--max-iterations", str(args.max_iterations)]
            started = time.perf_counter()
            try:
                completed = subprocess.run(command, cwd=root, env=env, text=True, capture_output=True, check=False, timeout=900)
                timed_out = False
            except subprocess.TimeoutExpired as error:
                completed = subprocess.CompletedProcess(command, 124, error.stdout or "", error.stderr or "benchmark timeout")
                timed_out = True
            duration = time.perf_counter() - started
            log_files = sorted(run_log_dir.glob("run_*.jsonl"), key=lambda path: path.stat().st_mtime, reverse=True)
            events = []
            if log_files:
                events = [json.loads(line) for line in log_files[0].read_text(encoding="utf-8").splitlines() if line.strip()]
            finished = next((event for event in reversed(events) if event.get("event") == "run_finished"), {})
            iterations = int(finished.get("total_iterations", 0)) if finished else None
            actions = sum(event.get("event") == "proposed_action" for event in events)
            blocked = sum(event.get("event") == "rejected" for event in events)
            success = (not timed_out and completed.returncode == 0 and "Objective achieved" in finished.get("summary", ""))
            records.append({"task_id": task["id"], "model": model,
                            "success": success,
                            "return_code": completed.returncode, "duration_seconds": round(duration, 3),
                            "timed_out": timed_out, "iterations": iterations,
                            "actions": actions, "blocked_actions": blocked, "stderr": str(completed.stderr)[-1000:]})
            write_results(records, output)
            print(f"{model} | {task['id']} | return={completed.returncode} | {duration:.1f}s")
    print(json.dumps(write_results(records, output), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
