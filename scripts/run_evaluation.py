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

from src.evaluation import summarize_guardrail_results, write_results
from src.guardrails import run_semantic_check, run_static_check


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate OpenClaw across tasks and model configurations")
    parser.add_argument("--tasks", default="evaluation/tasks.json")
    parser.add_argument("--models", nargs="+", default=[os.getenv("CODER_MODEL", "qwen2.5-coder:7b")])
    parser.add_argument("--planner-model", default=os.getenv("PLANNER_MODEL", "llama3.1:8b"))
    parser.add_argument("--max-iterations", type=int, default=4)
    parser.add_argument("--output", default="evaluation/results")
    parser.add_argument("--guardrail-only", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
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
    records = []
    for model in args.models:
        for task in tasks:
            workspace = root / "evaluation" / "workspaces" / f"{model.replace(':', '_')}_{task['id']}"
            if workspace.exists():
                shutil.rmtree(workspace)
            shutil.copytree(root / task["repo"], workspace)
            env = os.environ.copy()
            env.update({"CODER_MODEL": model, "PLANNER_MODEL": args.planner_model})
            command = [sys.executable, "main.py", "--objective", task["objective"], "--repo", str(workspace), "--max-iterations", str(args.max_iterations)]
            started = time.perf_counter()
            completed = subprocess.run(command, cwd=root, env=env, text=True, capture_output=True, check=False)
            duration = time.perf_counter() - started
            match = re.search(r"Total iterations: (\d+)", completed.stdout)
            rejected = re.findall(r"Actions rejected \((?:static|semantic)\):\s+(\d+)", completed.stdout)
            records.append({"task_id": task["id"], "model": model,
                            "success": completed.returncode == 0 and "NOT fully achieved" not in completed.stdout,
                            "return_code": completed.returncode, "duration_seconds": round(duration, 3),
                            "iterations": int(match.group(1)) if match else None,
                            "blocked_actions": sum(map(int, rejected)), "stderr": completed.stderr[-1000:]})
            print(f"{model} | {task['id']} | return={completed.returncode} | {duration:.1f}s")
    print(json.dumps(write_results(records, output), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
