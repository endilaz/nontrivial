"""Run-config loader/saver (CLAUDE.md ground rule 4: reproducibility).

Every experiment run gets a YAML config capturing the parameters that make the
run reproducible: model, provider, temperature, thinking/reasoning setting,
prompt template, seed, date, and prefill mechanism. The config is loaded before
a run and saved *alongside the run's outputs* so results can always be traced
back to the exact settings that produced them.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional, Union

import yaml
from pydantic import BaseModel


class RunConfig(BaseModel):
    """Reproducibility record for one experiment run.

    Fields mirror ground rule 4. ``thinking_setting`` is either a string label
    (``"disabled"`` / ``"none_supported"``) or a provider-specific dict; it is
    always recorded so the hidden-reasoning stratum can be reconstructed
    (CLAUDE.md Section 4).
    """

    run_id: str
    provider: str
    model: str
    provider_route: Optional[str] = None
    temperature: float = 0.0
    max_tokens: int = 512
    thinking_setting: Union[str, Dict[str, Any]] = "disabled"
    seed: Optional[int] = None
    prompt_template: str = "{problem}"
    prefill_mechanism: Optional[str] = None  # native_prefill | instruction_only | None
    prompt_template_version: str = "v1"
    date: str = ""
    notes: Optional[str] = None

    model_config = {"extra": "forbid"}


def load_run_config(path: Union[str, Path]) -> RunConfig:
    """Load and validate a YAML run config."""
    path = Path(path)
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return RunConfig.model_validate(data)


def save_run_config(config: RunConfig, path: Union[str, Path]) -> Path:
    """Write a run config to YAML (round-trips with :func:`load_run_config`)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(config.model_dump(), sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return path
