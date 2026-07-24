# Parser Validation Report (D3)

- Date: 2026-07-24
- Sample: `data/validation/parser_sample_v1.jsonl` (32 CoTs, 8/type, seed 20260723)
- Annotations: `data/validation/parser_annotations_v1.jsonl` (guide v1.1.0, researcher-labeled)
- Parser version: 1.2.0
- Scoring rule: guide §7 (boundary match ±2 chars, whitespace-only gap; macro-averaged per CoT)
- Gate: overall macro-F1 ≥ 0.90, per-type reported

## Results

**Overall macro-F1: 0.8866** (excluding long-CoT record: 0.8883)

| Task type | macro-F1 |
|---|---|
| math | 0.8009 |
| logic | 0.8912 |
| factual | 0.9583 |
| ethics | 0.8958 |

**Gate (0.90): FAIL as written — accepted deviation**

## Accepted deviation (researcher decision 2026-07-24)

The pre-registered gate is overall macro-F1 ≥ 0.90; the measured
value is 0.8866. The researcher reviewed the full disagreement
inventory and accepted the parser for Phase 3 use on the following grounds:

1. **All remaining disagreements are merge-convention differences on
   coherent corruption units** — whether filler/setup/givens lines (e.g.
   "The bed frame costs $75.", "We have two houses:", "Let's break it
   down step by step.") are separate steps or merge into the following
   operation. Both segmentations yield valid Phase 3 corruption targets.
   No disagreement involves a mid-sentence cut or two fused operations.
2. **Short-CoT arithmetic depresses math F1**: one ±1-boundary
   disagreement on a 3–5-boundary CoT caps that CoT's F1 at 0.67–0.86.
   Math disagreements are exclusively the setup-merge convention.
3. **No confounding of contrasts**: the same parser segments every CoT
   in every condition, so any systematic segmentation tendency affects
   all conditions equally and cannot bias corruption-sensitivity
   differences. Position labels (early/middle/late) shift by at most one
   step index on affected CoTs.
4. **Validation-set overfitting**: closing the remaining gap would mean
   encoding annotator-specific merge taste into parser heuristics tuned
   on the same 32 CoTs used for scoring, degrading the F1 estimate's
   validity on unseen CoTs.

Recorded in the Part I decisions log (2026-07-24) and as a writeup
limitation.

## Per-CoT detail

| task_id | type | long | parse method(s) | gold | pred | matched | P | R | F1 |
|---|---|---|---|---|---|---|---|---|---|
| math-004 | math |  | line | 3 | 4 | 3 | 0.750 | 1.000 | 0.857 |
| math-006 | math |  | line | 5 | 8 | 5 | 0.625 | 1.000 | 0.769 |
| math-019 | math |  | line | 3 | 4 | 3 | 0.750 | 1.000 | 0.857 |
| math-023 | math |  | line | 6 | 6 | 6 | 1.000 | 1.000 | 1.000 |
| math-035 | math |  | line | 4 | 5 | 3 | 0.600 | 0.750 | 0.667 |
| math-040 | math |  | line | 4 | 4 | 4 | 1.000 | 1.000 | 1.000 |
| math-042 | math |  | line | 3 | 4 | 3 | 0.750 | 1.000 | 0.857 |
| math-045 | math |  | line | 8 | 7 | 3 | 0.429 | 0.375 | 0.400 |
| logic-001 | logic |  | sentence | 1 | 1 | 1 | 1.000 | 1.000 | 1.000 |
| logic-003 | logic |  | sentence | 7 | 8 | 7 | 0.875 | 1.000 | 0.933 |
| logic-005 | logic | yes | sentence | 50 | 63 | 47 | 0.746 | 0.940 | 0.832 |
| logic-011 | logic |  | sentence | 4 | 5 | 4 | 0.800 | 1.000 | 0.889 |
| logic-025 | logic |  | sentence | 29 | 36 | 28 | 0.778 | 0.966 | 0.862 |
| logic-028 | logic |  | sentence | 7 | 6 | 5 | 0.833 | 0.714 | 0.769 |
| logic-033 | logic |  | sentence | 21 | 21 | 20 | 0.952 | 0.952 | 0.952 |
| logic-040 | logic |  | sentence | 30 | 35 | 29 | 0.829 | 0.967 | 0.892 |
| factual-018 | factual |  | sentence | 2 | 2 | 2 | 1.000 | 1.000 | 1.000 |
| factual-023 | factual |  | sentence | 2 | 2 | 2 | 1.000 | 1.000 | 1.000 |
| factual-028 | factual |  | sentence | 2 | 2 | 2 | 1.000 | 1.000 | 1.000 |
| factual-029 | factual |  | sentence | 0 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| factual-038 | factual |  | sentence | 1 | 1 | 1 | 1.000 | 1.000 | 1.000 |
| factual-042 | factual |  | sentence | 1 | 1 | 1 | 1.000 | 1.000 | 1.000 |
| factual-043 | factual |  | sentence | 0 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| factual-046 | factual |  | sentence | 1 | 2 | 1 | 0.500 | 1.000 | 0.667 |
| ethics-012 | ethics |  | sentence | 1 | 1 | 1 | 1.000 | 1.000 | 1.000 |
| ethics-016 | ethics |  | sentence | 0 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| ethics-017 | ethics |  | sentence | 0 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| ethics-019 | ethics |  | paragraph | 3 | 1 | 1 | 1.000 | 0.333 | 0.500 |
| ethics-023 | ethics |  | sentence | 0 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| ethics-034 | ethics |  | sentence | 0 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| ethics-037 | ethics |  | sentence | 0 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| ethics-042 | ethics |  | sentence | 1 | 2 | 1 | 0.500 | 1.000 | 0.667 |

## Notes

- Long-CoT records (guide §4 addendum) are scored on labeled head/tail
  regions only; the overall figure including them is reported alongside
  the short-CoT-only figure.
- Single-annotator labels with adjudication per guide §6; numbered-step
  prompting effects are a writeup limitation (guide §9).
