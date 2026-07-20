"""CoT Faithfulness Research — source package.

Phase 0 engineering modules:
- generation.py : provider-agnostic API client + disk response cache (ground rule 8)
- costs.py      : pre-batch cost estimator + cost log (ground rule 2)
- config.py     : YAML run-config loader/saver (ground rule 4)
- evaluation.py : answer extraction (Phase 0: `Final answer: X` parsing only)
- task_set.py   : Phase 1 benchmark task-set construction

See CLAUDE.md for the full project spec.
"""

from .task_set import build_task_set
