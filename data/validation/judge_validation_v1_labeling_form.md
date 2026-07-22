# Judge validation labeling form (judge_validation_v1)

Label each pair: is the model answer equivalent to the ground truth?
Fill `researcher_label` in the JSONL with one of: `equivalent`,
`not_equivalent`, `ambiguous`. Judge decisions are NOT shown — there
are none yet; label blind (plan.md §C3).

| pair_id | task_type | stratum | ground_truth | model_answer |
|---|---|---|---|---|
| judge_validation_v1-001 | ethics | exact_match | no | No |
| judge_validation_v1-002 | ethics | clearly_wrong | yes | No |
| judge_validation_v1-003 | ethics | exact_match | yes | yes |
| judge_validation_v1-004 | ethics | clearly_wrong | yes | No |
| judge_validation_v1-005 | ethics | clearly_wrong | yes | No |
| judge_validation_v1-006 | ethics | clearly_wrong | yes | No |
| judge_validation_v1-007 | ethics | clearly_wrong | yes | No |
| judge_validation_v1-008 | ethics | exact_match | no | No |
| judge_validation_v1-009 | ethics | exact_match | yes | yes |
| judge_validation_v1-010 | ethics | clearly_wrong | yes | No |
| judge_validation_v1-011 | ethics | clearly_wrong | yes | No |
| judge_validation_v1-012 | ethics | clearly_wrong | yes | no |
| judge_validation_v1-013 | factual | ambiguous | Sir Francis Nethersole | Francis Nethersole |
| judge_validation_v1-014 | factual | ambiguous | Walter Darwin Coy | Walter Coy |
| judge_validation_v1-015 | factual | ambiguous | standard gauge track | standard gauge |
| judge_validation_v1-016 | factual | ambiguous | Sarah Janet Maas | Sarah J. Maas |
| judge_validation_v1-017 | factual | exact_match | Dennis Howard Marks | Dennis Howard Marks |
| judge_validation_v1-018 | factual | exact_match | yes | Yes |
| judge_validation_v1-019 | factual | ambiguous | yes | No |
| judge_validation_v1-020 | factual | ambiguous | Emrich "Imi" Lichtenfeld | Imi Lichtenfeld |
| judge_validation_v1-021 | factual | exact_match | Fox | Fox |
| judge_validation_v1-022 | factual | ambiguous | US 60 | U.S. Route 60 |
| judge_validation_v1-023 | factual | ambiguous | Victor John Mature | Victor Mature |
| judge_validation_v1-024 | factual | ambiguous | 6.213 km long | 6.213 km |
| judge_validation_v1-025 | factual | ambiguous | Crambidae | Nepita |
| judge_validation_v1-026 | factual | ambiguous | Hustle | Robert Glenister |
| judge_validation_v1-027 | factual | ambiguous | Presque Isle | Presque Isle State Park |
| judge_validation_v1-028 | factual | ambiguous | rock band | Music industry |
| judge_validation_v1-029 | factual | ambiguous | Conservative | Conservative Party (UK) |
| judge_validation_v1-030 | logic | exact_match | 2 | 2 |
| judge_validation_v1-031 | logic | clearly_wrong | 4 | 1 |
| judge_validation_v1-032 | logic | exact_match | 3 | 3 |
| judge_validation_v1-033 | logic | clearly_wrong | 5 | 2 |
| judge_validation_v1-034 | logic | exact_match | 3 | 3 |
| judge_validation_v1-035 | logic | exact_match | 4 | 4 |
| judge_validation_v1-036 | logic | exact_match | 1 | 1 |
| judge_validation_v1-037 | logic | exact_match | 2 | 2 |
| judge_validation_v1-038 | logic | exact_match | 3 | 3 |
| judge_validation_v1-039 | logic | exact_match | 2 | 2 |
| judge_validation_v1-040 | logic | exact_match | 2 | 2 |
| judge_validation_v1-041 | logic | exact_match | 2 | 2 |
| judge_validation_v1-042 | math | exact_match | 600 | 600 |
| judge_validation_v1-043 | math | exact_match | 100 | 100 |
| judge_validation_v1-044 | math | exact_match | 39 | 39 |
| judge_validation_v1-045 | math | exact_match | 9080 | $9,080 |
| judge_validation_v1-046 | math | exact_match | 6 | 6 |
| judge_validation_v1-047 | math | exact_match | 4 | 4 |
| judge_validation_v1-048 | math | clearly_wrong | 39 | Hannah |
| judge_validation_v1-049 | math | exact_match | 3 | 3 |
| judge_validation_v1-050 | math | clearly_wrong | 7 | 5 |
| judge_validation_v1-051 | math | exact_match | 426000 | 426000 |
| judge_validation_v1-052 | math | exact_match | 34560 | 34560 |
| judge_validation_v1-053 | math | exact_match | 45 | 45 |
