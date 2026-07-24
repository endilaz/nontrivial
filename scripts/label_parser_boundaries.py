#!/usr/bin/env python3
"""Labeling aid for D3 boundary annotation (guide §8).

Workflow — no hand-counting of character offsets:

1. ``--worksheet``: print every sampled CoT split into numbered *candidate
   segments* (atomic units: hard lines, with single-line paragraphs further
   sentence-split). Save it:

       .venv/bin/python scripts/label_parser_boundaries.py --worksheet \
           > data/validation/parser_labeling_worksheet_v1.txt

2. For each CoT, write one decisions line listing the candidate numbers where
   a step **ends** (the last candidate of the CoT is always implied — do not
   list it). To merge adjacent candidates into one step, simply don't put a
   boundary between them. Format (one line per CoT, ``#`` comments allowed):

       math-004: 1 2 3 4
       logic-001: 2
       ethics-012:            # single-step CoT

3. ``--apply decisions.txt``: convert candidate numbers to exact character
   offsets, validate coverage, and fill in
   ``data/validation/parser_annotations_v1.jsonl`` (fails if the file was
   already annotated, unless ``--force``). To change a single annotation
   later, re-run ``--apply`` with a file containing only that line plus
   ``--force``.

Long-CoT protocol (guide amendment v1.0.0 §4 logic addendum, approved with the
guide): for CoTs longer than ``--long-threshold`` chars (default 4000), only
the first ``--long-head`` and last ``--long-tail`` candidates are shown and
labeled; the unshown middle is treated as correctly segmented at candidate
granularity for scoring (its boundaries are neither gold nor predicted).
Scoring for those CoTs uses only the labeled regions.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.parsing import split_reasoning  # noqa: E402

SAMPLE_PATH = PROJECT_ROOT / "data/validation/parser_sample_v1.jsonl"
ANNOTATION_PATH = PROJECT_ROOT / "data/validation/parser_annotations_v1.jsonl"

_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(\[*\-])")


def _candidate_spans(reasoning: str) -> list[tuple[int, int]]:
    """Atomic candidate segments: hard lines, single-line paragraphs
    sentence-split. Returns (start, end) spans covering the reasoning
    modulo whitespace gaps."""
    spans: list[tuple[int, int]] = []
    pos = 0
    lines = reasoning.splitlines(keepends=True)
    # Group lines into paragraphs (separated by blank lines).
    paragraphs: list[list[tuple[int, int]]] = [[]]
    for line in lines:
        s, e = pos, pos + len(line)
        pos = e
        if not line.strip():
            paragraphs.append([])
            continue
        paragraphs[-1].append((s, e))
    for para in paragraphs:
        if not para:
            continue
        if len(para) > 1:
            # Multi-line paragraph: each line is a candidate.
            for s, e in para:
                while s < e and reasoning[s].isspace():
                    s += 1
                while e > s and reasoning[e - 1].isspace():
                    e -= 1
                if s < e:
                    spans.append((s, e))
        else:
            # Single-line paragraph: sentence-split if multiple sentences.
            s, e = para[0]
            while s < e and reasoning[s].isspace():
                s += 1
            while e > s and reasoning[e - 1].isspace():
                e -= 1
            body = reasoning[s:e]
            matches = list(_SENTENCE_RE.finditer(body))
            if not matches:
                spans.append((s, e))
            else:
                starts = [s] + [s + m.end() for m in matches]
                ends = [s + m.start() + 1 for m in matches] + [e]
                for cs, ce in zip(starts, ends):
                    while cs < ce and reasoning[cs].isspace():
                        cs += 1
                    while ce > cs and reasoning[ce - 1].isspace():
                        ce -= 1
                    if cs < ce:
                        spans.append((cs, ce))
    return spans


def _load_sample() -> list[dict]:
    return [json.loads(l) for l in SAMPLE_PATH.read_text().splitlines()]


def _labeled_windows(
    n: int, head: int, tail: int
) -> tuple[range, range] | None:
    """Candidate index ranges to label for long CoTs, or None if short."""
    if n <= head + tail:
        return None
    return (range(0, head), range(n - tail, n))


def print_worksheet(head: int, tail: int, threshold: int) -> None:
    for rec in _load_sample():
        reasoning, _ = split_reasoning(rec["response_text"])
        spans = _candidate_spans(reasoning)
        windows = (
            None
            if len(reasoning) <= threshold
            else _labeled_windows(len(spans), head, tail)
        )
        print("=" * 78)
        print(f"### {rec['task_id']} ({rec['task_type']}) — "
              f"{len(spans)} candidates, {len(reasoning)} chars")
        if windows is not None:
            print(f"### LONG CoT: label candidates 0..{head - 1} and "
                  f"{len(spans) - tail}..{len(spans) - 1} only; middle is "
                  "scored as correctly segmented")
        shown = (
            set(range(len(spans)))
            if windows is None
            else set(windows[0]) | set(windows[1])
        )
        for i, (s, e) in enumerate(spans):
            if i not in shown:
                if i == min(set(range(len(spans))) - shown):
                    omitted = set(range(len(spans))) - shown
                    print(f"  [... candidates {min(omitted)}..{max(omitted)} "
                          f"omitted from labeling ...]")
                continue
            text = reasoning[s:e].replace("\n", " ")
            print(f"  [{i:>3}] {text}")
        print(f">>> {rec['task_id']}: <candidate numbers where steps end, "
              "space-separated; empty = single step>")
        print()


def _parse_decisions(text: str) -> dict[str, list[int]]:
    decisions: dict[str, list[int]] = {}
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        m = re.match(r"^([a-z]+-\d+)\s*:\s*(.*)$", line)
        if not m:
            raise SystemExit(f"decisions line {lineno} malformed: {raw!r}")
        task_id, rest = m.group(1), m.group(2).strip()
        nums = [int(x) for x in rest.split()] if rest else []
        if len(nums) != len(set(nums)) or nums != sorted(nums):
            raise SystemExit(
                f"decisions line {lineno}: numbers must be unique and sorted"
            )
        decisions[task_id] = nums
    return decisions


def _gold_boundaries(
    reasoning: str, spans: list[tuple[int, int]], decisions: list[int],
    windows, head: int, tail: int,
) -> tuple[list[int], list[int] | None]:
    """Convert step-end candidate numbers to char offsets.

    Returns (gold_boundaries, labeled_region) where labeled_region is
    None for short CoTs or (head_end_char, tail_start_char) for long ones —
    scoring uses only boundaries inside labeled regions.
    """
    n = len(spans)
    for d in decisions:
        if not (0 <= d < n - 1):
            raise SystemExit(
                f"candidate {d} invalid (must be 0..{n - 2}; the final "
                "candidate's end is never a boundary)"
            )
    ends = [spans[d][1] for d in decisions]
    if windows is None:
        return ends, None
    head_end = spans[head - 1][1] if head > 0 else 0
    tail_start = spans[n - tail][0]
    return ends, (head_end, tail_start)


def apply_decisions(
    decisions_path: Path, head: int, tail: int, threshold: int,
    annotator: str, force: bool,
) -> None:
    decisions = _parse_decisions(decisions_path.read_text())
    sample = {r["task_id"]: r for r in _load_sample()}
    unknown = set(decisions) - set(sample)
    if unknown:
        raise SystemExit(f"decisions reference unknown tasks: {sorted(unknown)}")
    missing = set(sample) - set(decisions)
    if missing and not force:
        raise SystemExit(
            f"decisions missing {len(missing)} tasks (e.g. "
            f"{sorted(missing)[:3]}); provide all 32 or use --force to "
            "update a subset"
        )

    annotations = [
        json.loads(l) for l in ANNOTATION_PATH.read_text().splitlines()
    ]
    if any(a["annotator"] for a in annotations) and not force:
        raise SystemExit(
            "annotation file already contains filled annotations; use "
            "--force to overwrite"
        )
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    n_filled = 0
    for a in annotations:
        tid = a["task_id"]
        if tid not in decisions:
            continue
        rec = sample[tid]
        reasoning, _ = split_reasoning(rec["response_text"])
        spans = _candidate_spans(reasoning)
        windows = (
            None
            if len(reasoning) <= threshold
            else _labeled_windows(len(spans), head, tail)
        )
        gold, region = _gold_boundaries(
            reasoning, spans, decisions[tid], windows, head, tail
        )
        a["boundaries"] = gold
        a["n_steps"] = len(decisions[tid]) + 1
        a["annotator"] = annotator
        a["annotated_at"] = now
        a["adjudicated"] = True
        if region is not None:
            a["labeled_region"] = {
                "head_end_char": region[0],
                "tail_start_char": region[1],
                "protocol": f"long-cot head={head} tail={tail}",
            }
        n_filled += 1
    ANNOTATION_PATH.write_text(
        "".join(json.dumps(a, sort_keys=True) + "\n" for a in annotations)
    )
    print(f"filled {n_filled} annotations in {ANNOTATION_PATH}")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--worksheet", action="store_true",
                   help="print the labeling worksheet")
    p.add_argument("--apply", type=Path, metavar="DECISIONS.txt",
                   help="apply a decisions file to the annotation JSONL")
    p.add_argument("--annotator", default="researcher")
    p.add_argument("--long-threshold", type=int, default=4000)
    p.add_argument("--long-head", type=int, default=40)
    p.add_argument("--long-tail", type=int, default=20)
    p.add_argument("--force", action="store_true")
    args = p.parse_args()

    if args.worksheet:
        print_worksheet(args.long_head, args.long_tail, args.long_threshold)
    elif args.apply:
        apply_decisions(args.apply, args.long_head, args.long_tail,
                        args.long_threshold, args.annotator, args.force)
    else:
        p.error("one of --worksheet or --apply is required")


if __name__ == "__main__":
    main()
