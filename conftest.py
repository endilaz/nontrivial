"""pytest bootstrap.

Ensures the repo root is on ``sys.path`` so tests can ``import src.<module>``
regardless of the working directory pytest is invoked from. pytest already adds
the directory containing this conftest to ``sys.path``, but we make it explicit
so ad-hoc ``python -m pytest`` invocations behave identically.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
