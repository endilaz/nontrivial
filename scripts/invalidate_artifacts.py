"""Preserve-and-invalidate the 2026-07-21 audited artifacts (Milestone A2).

Moves the invalidated generated task set, baseline/smoke results, v1 cache
receipts, and cost ledger into dated ``invalidated/2026-07-21/`` directories and
writes a SHA-256 audit manifest. Idempotent and reversible-by-review: it never
deletes an artifact (only moves it) and never touches downloaded source datasets.

Usage:
    python scripts/invalidate_artifacts.py --dry-run   # preview the plan
    python scripts/invalidate_artifacts.py             # apply

Exit codes: 0 = applied/planned cleanly; 2 = conflict requiring reconciliation.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional, Sequence

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src import invalidation  # noqa: E402
from src.invalidation import InvalidationConflictError  # noqa: E402


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        default=str(REPO_ROOT),
        help="Repository root to operate on (default: this repo).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the plan without moving anything or writing the manifest.",
    )
    parser.add_argument(
        "--manifest",
        default=invalidation.DEFAULT_MANIFEST_REL,
        help="Manifest path relative to --root.",
    )
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    root = Path(args.root)

    try:
        result = invalidation.execute(
            root, dry_run=args.dry_run, manifest_rel=args.manifest
        )
    except InvalidationConflictError as exc:
        print(f"CONFLICT: {exc}", file=sys.stderr)
        print(
            "An original artifact differs from its preserved invalidated copy. "
            "Reconcile manually before re-running.",
            file=sys.stderr,
        )
        return 2

    mode = "DRY RUN — nothing changed" if result.dry_run else "APPLIED"
    print(f"Invalidation ({invalidation.INVALIDATION_DATE}) — {mode}")
    print(f"  to move now:          {len(result.moved)}")
    print(f"  already invalidated:  {len(result.already)}")
    for a in result.moved:
        print(f"    move  {a.original_path}  ->  {a.invalidated_path}")
    for a in result.already:
        note = " (removing redundant leftover original)" if a.duplicate_original else ""
        print(f"    kept  {a.invalidated_path}{note}")

    if not result.dry_run:
        print(f"  manifest: {result.manifest_path}")
        print(f"  artifacts recorded: {result.manifest['artifact_count']}")
    else:
        print("  (re-run without --dry-run to apply)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
