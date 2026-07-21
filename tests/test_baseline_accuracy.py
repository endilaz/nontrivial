"""Tests for the conservative deterministic scoring in scripts.baseline_accuracy."""

from scripts.baseline_accuracy import ScoreResult, filter_tasks, score_answer


def test_score_answer_numeric_exact_ignores_commas():
    score = score_answer("56,551", "56551", "short_text")
    assert score == ScoreResult(
        is_correct=True,
        method="numeric_exact",
        normalized_prediction="56551",
        normalized_ground_truth="56551",
    )


def test_score_answer_yes_no_normalizes_case_and_punctuation():
    score = score_answer("Yes.", "yes", "yes_no")
    assert score.is_correct is True
    assert score.method == "yes_no_exact"
    assert score.normalized_prediction == "yes"
    assert score.normalized_ground_truth == "yes"


def test_score_answer_short_text_is_conservative_normalized_exact():
    score = score_answer("  New   York City. ", "new york city", "short_text")
    assert score.is_correct is True
    assert score.method == "normalized_string_exact"
    assert score.normalized_prediction == "new york city"


def test_score_answer_number_requires_parseable_number():
    score = score_answer("about 12", "12", "number")
    assert score.is_correct is False
    assert score.method == "numeric_exact"
    assert score.normalized_prediction is None
    assert score.normalized_ground_truth == "12"


def test_filter_tasks_filters_by_type_and_limit():
    tasks = [
        {"id": "math-001", "task_type": "math", "problem": "p", "ground_truth": "1", "answer_format": "number"},
        {"id": "logic-001", "task_type": "logic", "problem": "p", "ground_truth": "1", "answer_format": "short_text"},
        {"id": "math-002", "task_type": "math", "problem": "p", "ground_truth": "2", "answer_format": "number"},
    ]
    filtered = filter_tasks(tasks, task_types=["math"], limit=1)
    assert [task["id"] for task in filtered] == ["math-001"]