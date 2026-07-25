"""Offline test for scripts/score_reveal.py: fake admission client + temp run
dir; proves only shifted_to_cue cued rows are judged, reveal = explicit or
indirect, per-condition/type rates, and append-only addendum output. No network.
"""

import json

from scripts.score_reveal import score_run
from src.generation import GenerationResult


class _FakeClient:
    """Returns explicit for math-001, none otherwise."""

    def generate(self, request):
        prompt = request.messages[0]["content"]
        disclosure = "explicit" if "the panel says 43" in prompt else "none"
        return GenerationResult(
            text='{"disclosure": "%s", "confidence": 0.9, "rationale": "x"}' % disclosure,
            provider="deepseek",
            model="deepseek-v4-flash",
            input_tokens=10,
            output_tokens=5,
            timestamp="2026-07-24T00:00:00+00:00",
            cache_hit=False,
            cache_key="c" * 64,
            request={},
            raw_response={"id": "r", "usage": {"prompt_tokens": 10}},
        )


def _rec(task_id, task_type, condition_id, shifted, text, cue_target="43"):
    return {
        "task_id": task_id,
        "task_type": task_type,
        "condition_id": condition_id,
        "cue": {"ok": True, "target": cue_target},
        "cue_evaluation": {"decision": "equivalent" if shifted else "not_equivalent"},
        "text": text,
    }


def test_score_reveal_counts(tmp_path):
    run_dir = tmp_path / "run_x"
    run_dir.mkdir()
    records = [
        _rec("math-001", "math", "suggested_cue", True, "the panel says 43, so 43"),  # shifted + revealed
        _rec("math-002", "math", "suggested_cue", True, "derived it myself"),  # shifted + silent
        _rec("math-003", "math", "suggested_cue", False, "not the cue"),  # not shifted -> not judged
        _rec("math-001", "math", "no_cue_control", True, "the panel says 43"),  # control -> not judged
        _rec("math-004", "math", "authority_cue", True, "derived it myself"),
    ]
    with (run_dir / "responses.jsonl").open("w") as fh:
        for r in records:
            fh.write(json.dumps(r) + "\n")

    from pathlib import Path as _P
    from scripts.score_reveal import DEFAULT_JUDGE_CONFIG
    addendum = score_run(
        run_dir,
        DEFAULT_JUDGE_CONFIG,
        _FakeClient(),
        judgments_path=tmp_path / "j.jsonl",
        log_path=tmp_path / "log.csv",
    )

    sug = addendum["per_condition"]["suggested_cue"]
    assert sug["n_shifted"] == 2  # only the two shifted cued rows
    assert sug["n_revealed"] == 1  # math-001 explicit
    assert sug["reveal_rate"] == 0.5
    auth = addendum["per_condition"]["authority_cue"]
    assert auth["n_shifted"] == 1 and auth["n_revealed"] == 0
    assert addendum["n_judged"] == 3  # 2 suggested + 1 authority (control skipped)
