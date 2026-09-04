from src.evaluation import classification_metrics, summarize_task_results


def test_classification_metrics():
    result = classification_metrics([True, True, False, False], [True, False, True, False])
    assert result["true_positive"] == 1
    assert result["true_negative"] == 1
    assert result["false_positive"] == 1
    assert result["false_negative"] == 1
    assert result["precision"] == 50.0
    assert result["recall"] == 50.0


def test_task_summary_by_model():
    result = summarize_task_results([
        {"model": "a", "success": True, "iterations": 2, "duration_seconds": 4, "blocked_actions": 1},
        {"model": "a", "success": False, "iterations": 4, "duration_seconds": 6, "blocked_actions": 2},
    ])
    assert result["by_model"]["a"]["success_rate"] == 50.0
    assert result["by_model"]["a"]["average_iterations"] == 3.0
    assert result["by_model"]["a"]["blocked_actions"] == 3
