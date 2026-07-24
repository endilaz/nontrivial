# CoT Step-Boundary Annotation Guide (v1.0.0)

**Status:** Approved by the researcher 2026-07-23 (Milestone D2 gate, Part II
§8 D2). This guide fixes what counts as one reasoning step, how boundaries are
matched for scoring, and how uncertain cases are adjudicated. The parser F1
reported in D3 is defined by this document; do not change the rules after
seeing results.

---

## 1. Purpose

Phase 3 step corruption requires splitting a model's visible chain-of-thought
(CoT) into discrete steps with exact character spans. To validate the parser
(D3), the researcher hand-labels step boundaries on 32 sampled CoTs (8 per task
type) and the parser's boundaries are scored against those labels. This guide is
the labeling authority.

## 2. What is being segmented

- Input: the **visible reasoning text** of one baseline response — everything
  before the `Final answer:` line. The final-answer line itself is **never**
  part of any step and is excluded before labeling.
- Output: an ordered list of steps. Each step is a contiguous span of the
  reasoning text. Every non-whitespace character of the reasoning text belongs
  to exactly one step (coverage), and steps do not overlap.

## 3. Definition of a step

> **A step is the smallest text unit that states one complete reasoning
> operation or assertion** — a single calculation, a single deduction from
> clue(s), a single retrieved fact or comparison, or a single evaluative
> claim — such that deleting or altering it would change the downstream
> reasoning.

Practical consequences:

- **Merge** text that jointly performs one operation (e.g., setting up and
  executing one arithmetic calculation across two short hard-broken lines;
  attributing a clue and quoting it).
- **Split** between two successive operations, even when they share a
  paragraph or line, when each could independently be corrupted (e.g.,
  "15 minutes is 0.25 hours." | "Distance = 40 × 0.25 = 10 miles." are two
  steps).
- When genuinely ambiguous whether two assertions form one operation or two,
  prefer **splitting** (see §6 adjudication).

## 4. Per-task-type guidance

### Math
- Each arithmetic operation or unit conversion is one step, including its
  immediate setup phrase (e.g., "Total sold = 60 + 10 + 20 + 44 + 66 = 200").
- A data restatement/list that only copies givens (e.g., a per-day sales list)
  counts as **one** step total, not one per line, unless a calculation is
  applied line-by-line.
- A final "the problem asks for X, so the answer is X" sentence before the
  `Final answer:` line **is** a reasoning step (it restates the conclusion);
  label it.

### Logic (grid puzzles)
- One step per derived placement or elimination. "From clues 3 and 7, X is in
  house 2" is one step even though it uses two clues.
- Clue attribution + quoted clue text belongs to the same step as the
  deduction drawn from it.
- Pure verification restatements at the end ("Checking: all clues satisfied")
  count as one step.

### Factual (HotpotQA)
- One step per retrieved fact, per comparison/synthesis, and per source
  citation that carries new evidence.
- Bridge-entity identification ("X directed Y, so we need X's birth year") is
  its own step.
- A closing synthesis sentence ("Since 1587 < 1596, Francis was born first")
  is one step even if it repeats earlier facts.

### Ethics (commonsense)
- Often only 1–3 steps. The stance assertion and each distinct justification
  (deception, harm, consent, etc.) are separate steps when stated separately.
- Do not split a single justification clause into sub-clauses.

### Long-CoT protocol (addendum, approved 2026-07-23 with the labeling aid)

Some logic baselines enumerate whole constraint grids (one sampled CoT exceeds
27k chars / 460 atomic lines). Full hand labeling of such CoTs is intractable
and its F1 contribution would be dominated by mechanical line boundaries.
Protocol: for CoTs longer than **4000 chars**, the annotator labels only the
**first 40 and last 20 candidate segments** (atomic units defined by the
labeling aid: hard lines, with single-line multi-sentence paragraphs
sentence-split). The unlabeled middle is treated as correctly segmented at
candidate granularity for scoring: boundaries inside it count as neither gold
nor predicted. The annotation record stores `labeled_region`
(`head_end_char`, `tail_start_char`, protocol string), and the D3 report
flags these CoTs and reports F1 with and without them.

## 5. Boundary placement rules

- Boundaries fall **between** steps; trailing whitespace and blank lines are
  not part of any step.
- Numbered/list markers (`1.`, `2)`, `Step 3:`) belong to the step they head.
- Markdown emphasis, bullets, and quote characters stay with their step text.
- Leading preamble before a first numbered item (e.g., "Let me think.") is its
  own step if it asserts something task-relevant; if it is pure filler with no
  assertion, it is still labeled as its own step (so coverage holds) but note
  it as "filler" in the annotation notes column.

## 6. Adjudication procedure

1. Annotate independently using §3–§5; record a confidence (`sure`/`unsure`)
   per boundary.
2. For every `unsure` boundary, write a one-line reason.
3. Adjudication pass: re-read the unsure cases after finishing the full 32-CoT
   set (so judgments are calibrated across types), apply the tie-break rule:
   **when two readings are defensible, choose the one with more, smaller
   steps.** Record the final decision and reason in the annotation file.
4. Any case still unresolved after the tie-break is marked `adjudicated: no`
   and excluded from F1 denominators for both precision and recall, and
   reported in the D3 report.

## 7. Boundary-matching rule for F1 (frozen)

Scoring is **boundary-level**, computed per CoT then averaged (macro) within
task type and overall:

- The set of gold boundaries = the set of character offsets where one gold
  step ends and the next begins (i.e., `end_char` of every non-final gold
  step). Same for predicted boundaries.
- A predicted boundary **matches** a gold boundary iff the predicted step
  ending there covers exactly the same reasoning content — operationally:
  `|pred_end − gold_end| ≤ 2` characters **and** the intervening text between
  the two offsets is whitespace-only. (Tolerance exists only for
  trailing-whitespace placement, not for boundary disagreement.)
- Precision = matched / predicted boundaries; recall = matched / gold
  boundaries; F1 = harmonic mean. A CoT with a single gold step and a single
  predicted step contributes F1 = 1 (no boundaries on either side); a
  single-gold-step CoT split into k > 1 predicted steps contributes precision
  0 for that CoT's boundary set with recall undefined → counted as F1 = 0.
  (Rule: over- or under-segmenting a boundary-free CoT is a complete miss.)
- The LLM-fallback flag and parse method play no role in scoring; only
  boundaries count.

Acceptance (Part II §8 D3): overall macro-F1 ≥ 0.90, with per-type F1 reported
(no type may be hidden by the aggregate).

## 8. Annotation file format

One JSONL record per CoT, written by the D3 tooling:

```json
{
  "schema": "cot_boundary_annotation",
  "schema_version": "1.0.0",
  "task_id": "math-001",
  "task_type": "math",
  "source_run_id": "baseline_deepseek_v4_flash_v2_amend2",
  "response_key": ["math-001", "baseline", "instruction_only", 0],
  "guide_version": "1.0.0",
  "annotator": "researcher",
  "annotated_at": "UTC ISO-8601",
  "boundaries": [145, 289],
  "n_steps": 3,
  "unsure_boundaries": [],
  "notes": "string or null",
  "adjudicated": true,
  "labeled_region": null
}
```

`boundaries` are gold `end_char` offsets (into the reasoning text) of every
non-final step, in increasing order. `labeled_region` is non-null only for
long CoTs (§4 addendum) and restricts scoring to the labeled head/tail
regions. The annotation file is immutable once the
D3 sample is frozen; corrections require a new `guide_version` and a recorded
deviation.

## 9. Known limitations (for the writeup)

- Prompting models to number their steps (Phase 2 strategy 1) changes the CoT
  distribution being studied; numbered CoTs are also easier to parse, so D3
  F1 may overstate performance on natural unnumbered CoT.
- Boundary labels are single-annotator (researcher); no inter-annotator
  agreement is measurable. The adjudication pass and tie-break rule are the
  mitigation.
