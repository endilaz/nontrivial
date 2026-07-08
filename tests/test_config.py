"""Tests for the YAML run-config round-trip (ground rule 4)."""

from pathlib import Path

from src.config import RunConfig, load_run_config, save_run_config

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_yaml_roundtrip_string_thinking(tmp_path):
    cfg = RunConfig(
        run_id="rt1",
        provider="openai",
        model="gpt-5.4-mini",
        temperature=0.0,
        max_tokens=512,
        thinking_setting="disabled",
        seed=7,
        prompt_template="Solve: {problem}\nFinal answer:",
        prefill_mechanism=None,
        date="2026-07-08",
        notes="round-trip test",
    )
    path = tmp_path / "cfg.yaml"
    save_run_config(cfg, path)
    loaded = load_run_config(path)
    assert loaded == cfg


def test_yaml_roundtrip_dict_thinking(tmp_path):
    cfg = RunConfig(
        run_id="rt2",
        provider="anthropic",
        model="claude-sonnet-4-6",
        thinking_setting={"type": "adaptive", "budget": 0},
        seed=None,
        prefill_mechanism="native_prefill",
    )
    path = tmp_path / "cfg.yaml"
    save_run_config(cfg, path)
    loaded = load_run_config(path)
    assert loaded == cfg
    assert isinstance(loaded.thinking_setting, dict)


def test_smoke_test_config_is_valid():
    # The committed example config must load and validate.
    cfg = load_run_config(REPO_ROOT / "configs" / "smoke_test.yaml")
    assert cfg.run_id == "smoke_test_v1"
    assert cfg.provider == "deepseek"  # approved workhorse (2026-07-08)
    assert cfg.model == "deepseek-v4-flash"
    assert "{problem}" in cfg.prompt_template
