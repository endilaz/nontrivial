# CoT Faithfulness Benchmark Notes

## Benchmark Structure

-   4 task types × 50 problems = **200 total**
    -   Math (GSM8K-derived)
    -   Logic
    -   Multi-step factual
    -   Ethical dilemmas
-   Standardized output: final line must be `Final answer: X`.
-   Record metadata:
    -   problem ID
    -   task type
    -   source dataset
    -   original dataset ID (if applicable)
    -   perturbation seed
    -   perturbation type(s)
    -   ground truth (when applicable)
    -   license
    -   answer format
    -   `binary_ok` flag

## Recommended Sources

### Math

-   GSM8K, with **surface-level perturbations**.

### Logic

-   BIG-bench reasoning tasks
-   PrOntoQA
-   Zebra/Einstein puzzles

### Multi-step factual

-   HotpotQA (recommended)
-   2WikiMultihopQA
-   MuSiQue

### Ethics

-   ETHICS (recommended)
-   Social Chemistry 101
-   Moral Stories

## Surface-Level Perturbation

Goal: change wording while preserving reasoning.

Safe changes: - names - objects - locations - wording - compatible
numbers

Never change: - operation sequence - reasoning graph - inference depth -
required mathematical operations

Example: Original: 18 → subtract 7 → add 12

Perturbed: 24 → subtract 9 → add 14

Reasoning stays identical.

## Perturbation Seeds

A perturbation seed is the random seed used to generate the rewritten
problem.

Benefits: - reproducibility - exact regeneration - easier debugging

Example metadata: - original_id: gsm8k_train_4178 - perturbation_seed:
84721 - perturbation_types: entity, numbers, wording

## GSM8K Pipeline

1.  Sample problems.
2.  Record original IDs.
3.  Apply surface perturbations.
4.  Verify reasoning graph unchanged.
5.  Solve to verify correctness.
6.  Store metadata.

## Binary Subset

Reserve at least 20 problems per task that naturally admit Yes/No
answers.

Purpose: - enables probing experiments on hidden representations -
simplifies labels (binary classification) - avoids forcing the whole
benchmark into binary format

Do **not** retrofit arbitrary thresholds later.

Preferred: "Can the family afford the trip?"

Instead of: "Is the answer \>173?"

## Binary GSM8K Conversion

Compute the same intermediate value, then ask a natural decision
question.

Example: Original: "What is the total cost?"

Binary: "Does she have enough money to pay?"

Reasoning remains identical.

## Evaluation

-   Math, logic, factual:
    -   correctness
    -   answer changes after CoT corruption
-   Ethics:
    -   answer stability only
    -   no objective correctness

## Suggested Operational Definitions

-   Logic: requires only prompt information, no external knowledge.
-   Multi-hop factual: requires combining at least two independent
    facts.
-   Preserve "reasoning graph" when perturbing.
-   Problems should be self-contained and uniquely answerable (except
    ethics).
