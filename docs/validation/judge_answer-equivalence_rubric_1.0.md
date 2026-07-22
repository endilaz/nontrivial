# Judge validation: answer-equivalence rubric 1.0.0

- Judge model: `deepseek/deepseek-v4-flash`, temperature 0, confidence threshold 0.5
- Prompt SHA-256: `0a504455d3459aec…`
- Split: dev (holdout=false); holdout untouched — no prompt tuning performed
- Dev pairs: 26 (ambiguous labels excluded: 0; judge abstentions: 0)

## Confusion (decided pairs)

| | judge: equivalent | judge: not_equivalent |
|---|---|---|
| **label: equivalent** | 19 | 0 |
| **label: not_equivalent** | 0 | 7 |

- Sensitivity: 1.000  Specificity: 1.000  Accuracy: 1.000

## By task type

| type | n | ambiguous | abstain | sensitivity | specificity | accuracy |
|---|---|---|---|---|---|---|
| ethics | 5 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| factual | 7 | 0 | 0 | 1.000 | n/a | 1.000 |
| logic | 6 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| math | 8 | 0 | 0 | 1.000 | 1.000 | 1.000 |

## Disagreements (decided pairs)

None.

## Notes

- Judge config remains `enabled: false` until the researcher approves an
  accuracy threshold and tie policy (plan.md §C1/C3).
- Pairs for tombstoned tasks factual-041/044 are labeled `ambiguous` and
  excluded from denominators (both sit in the untouched holdout split).
