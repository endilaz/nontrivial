"""Dataset license registry (Milestone B6).

Single source of truth for source-dataset license terms. Data licenses are
recorded separately from code-repository licenses — they are not the same
thing (e.g. HotpotQA: data CC BY-SA 4.0, code Apache-2.0).

Every task record's ``source_license`` field and the task-set manifest's
license block come from this registry. ``license_for_source`` fails closed on
an unknown source so no record silently ships ``license: unknown``.
"""

from __future__ import annotations

from typing import Dict

#: Dataset name -> license facts. ``retrieved`` is the date the terms were
#: verified from the cited URL.
DATASET_LICENSES: Dict[str, Dict[str, str]] = {
    "GSM8K": {
        "license": "MIT",
        "url": "https://github.com/openai/grade-school-math/blob/master/LICENSE",
        "retrieved": "2026-07-21",
        "attribution": "Cobbe et al. 2021, arXiv:2110.14168",
        "notes": "Single MIT LICENSE file in the releasing repository covers the released train/test data files.",
    },
    "BIG-Bench logic_grid_puzzle": {
        "license": "Apache-2.0",
        "url": "https://github.com/google/BIG-bench/blob/main/LICENSE",
        "retrieved": "2026-07-21",
        "attribution": "BIG-bench authors 2023, TMLR, arXiv:2206.04615",
        "notes": "Benchmark tasks ship inside the Apache-2.0-licensed BIG-bench repository.",
    },
    "HotpotQA": {
        "license": "CC BY-SA 4.0",
        "url": "https://github.com/hotpotqa/hotpot/blob/master/README.md#license",
        "retrieved": "2026-07-21",
        "attribution": "Yang et al. 2018, EMNLP, arXiv:1809.09600",
        "notes": (
            "Dataset is CC BY-SA 4.0 per the official README; the accompanying "
            "code is Apache-2.0. Do not conflate the two. ShareAlike applies to "
            "redistributed derived task records."
        ),
        "code_license": "Apache-2.0",
    },
    "ETHICS": {
        "license": "MIT",
        "url": "https://github.com/hendrycks/ethics/blob/master/LICENSE",
        "retrieved": "2026-07-21",
        "attribution": "Hendrycks et al. 2021, ICLR, arXiv:2008.02275",
        "notes": "MIT license covers the repository including the commonsense morality CSVs.",
    },
}


class UnknownDatasetLicenseError(KeyError):
    """No verified license on record for the requested dataset."""


def license_for_source(source: str) -> Dict[str, str]:
    """Return verified license facts for ``source`` or fail closed."""
    if source not in DATASET_LICENSES:
        raise UnknownDatasetLicenseError(
            f"no verified license for {source!r}; resolve terms in B6 before building records"
        )
    return dict(DATASET_LICENSES[source])


def license_tag(source: str) -> str:
    """Short ``name (URL)`` tag stored on each task record."""
    facts = license_for_source(source)
    return f"{facts['license']} ({facts['url']})"
