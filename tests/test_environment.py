"""Environment-reproducibility guards (Milestone A1).

These are offline regression checks for the two A1 invariants that are easy to
break silently:

1. A transitive lock exists and records the interpreter/platform it was resolved
   against, so a fresh checkout can be recreated deterministically.
2. The `live_api` marker is registered AND excluded from the default suite, so a
   paid/network test can never run under a bare `pytest -q`.
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LOCK = REPO_ROOT / "requirements.lock.txt"


def test_transitive_lock_exists_and_records_platform():
    assert LOCK.is_file(), "requirements.lock.txt (A1 transitive lock) is missing"
    text = LOCK.read_text(encoding="utf-8")
    # Platform provenance must be recorded so recreation is deterministic.
    assert "Python:" in text and "3.13.5" in text
    assert "Architecture:" in text and "arm64" in text
    assert "Darwin" in text
    # A few representative direct pins must be present in the resolved set.
    for pin in ("anthropic==0.116.0", "openai==2.44.0", "pydantic==2.13.4", "pytest==9.1.1"):
        assert pin in text, f"expected {pin!r} in the lock"


def test_lock_pins_are_fully_version_specified():
    # Every requirement line must be an exact `==` pin (no ranges / unpinned).
    for line in LOCK.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        assert "==" in line, f"lock line is not an exact pin: {line!r}"


def test_live_api_marker_registered_and_excluded_by_default(pytestconfig):
    # The marker must be registered (so --strict-markers accepts it)...
    markers = pytestconfig.getini("markers")
    assert any(m.startswith("live_api") for m in markers), (
        "live_api marker is not registered in pytest.ini"
    )
    # ...and the default invocation must deselect it. addopts is what makes a
    # bare `pytest -q` skip live tests; if this pin is removed, live/network
    # tests would silently run by default.
    addopts = pytestconfig.getini("addopts")
    joined = " ".join(addopts)
    assert "not live_api" in joined, (
        "pytest.ini addopts no longer excludes live_api from the default suite"
    )
    assert "--strict-markers" in addopts, (
        "pytest.ini no longer enforces --strict-markers"
    )
