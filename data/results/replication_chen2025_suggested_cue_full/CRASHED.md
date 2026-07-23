# Run crashed 2026-07-23 (do not analyze as canonical)

306 responses persisted; run aborted at math-008 by a cue-target formatting bug
(scientific notation `4E+1` rejected by the C1 evaluator), fixed in commit
1036fd1. A5 manifest semantics forbid resuming under a different code revision,
so the canonical run is `replication_chen2025_suggested_cue_full_v2`. The 301
logged calls ($0.0548) hit the response cache in v2 and cost 0 there. math-008's
crashed call was generated but never persisted/logged (post-generation,
pre-evaluation crash) — at most one unlogged call, consistent with the A2
reconciliation caveat.
