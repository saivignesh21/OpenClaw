"""Metrics for reproducible OpenClaw benchmark experiments."""

from __future__ import annotations

import csv
import json
from statistics import median
from collections.abc import Iterable
from pathlib import Path
from typing import Any


def classification_metrics(expected: Iterable[bool], predicted: Iterable[bool]) -> dict[str, float | int]:
    pairs = list(zip(expected, predicted))
    tp = sum(e and p for e, p in pairs)
    tn = sum(not e and not p for e, p in pairs)
    fp = sum(not e and p for e, p in pairs)
    fn = sum(e and not p for e, p in pairs)
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    false_positive_rate = fp / (fp + tn) if fp + tn else 0.0
    return {"count": len(pairs), "true_positive": tp, "true_negative": tn,
            "false_positive": fp, "false_negative": fn,
            "precision": round(precision * 100, 2), "recall": round(recall * 100, 2),
            "f1": round(f1 * 100, 2), "false_positive_rate": round(false_positive_rate * 100, 2)}


def summarize_task_results(records: list[dict[str, Any]]) -> dict[str, Any]:
    by_model: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        by_model.setdefault(str(record.get("model", "unknown")), []).append(record)

    def summarize(items: list[dict[str, Any]]) -> dict[str, Any]:
        successes = [bool(item.get("success", False)) for item in items]
        durations = [float(item["duration_seconds"]) for item in items if item.get("duration_seconds") is not None]
        iterations = [int(item["iterations"]) for item in items if item.get("iterations") is not None]
        action_counts = [int(item["actions"]) for item in items if item.get("actions") is not None]
        return {"tasks": len(items), "successes": sum(successes),
                "success_rate": round(sum(successes) / len(items) * 100, 2) if items else 0.0,
                "average_iterations": round(sum(iterations) / len(iterations), 2) if iterations else 0.0,
                "median_iterations": median(iterations) if iterations else 0.0,
                "average_execution_seconds": round(sum(durations) / len(durations), 2) if durations else 0.0,
                "median_execution_seconds": median(durations) if durations else 0.0,
                "average_actions": round(sum(action_counts) / len(action_counts), 2) if action_counts else 0.0,
                "blocked_actions": sum(int(item.get("blocked_actions", 0)) for item in items),
                "average_blocked_actions": round(sum(int(item.get("blocked_actions", 0)) for item in items) / len(items), 2) if items else 0.0}

    return {"overall": summarize(records), "by_model": {m: summarize(v) for m, v in by_model.items()}}


def summarize_guardrail_results(records: list[dict[str, Any]]) -> dict[str, Any]:
    return {checker: classification_metrics(
        [bool(row["expected_allowed"]) for row in records if row.get(f"{checker}_allowed") is not None],
        [bool(row[f"{checker}_allowed"]) for row in records if row.get(f"{checker}_allowed") is not None],
    ) for checker in ("static", "semantic")}


def write_results(records: list[dict[str, Any]], output_dir: str | Path) -> dict[str, Any]:
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "results.json").write_text(json.dumps(records, indent=2), encoding="utf-8")
    if records:
        with (destination / "results.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=sorted({key for row in records for key in row}))
            writer.writeheader()
            writer.writerows(records)
    summary = summarize_task_results(records)
    (destination / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary
