"""Layering rules and configuration precedence.

The import rule is checked mechanically rather than trusted to review: it is
what keeps a future headless runner or IDE frontend a matter of adding a
subscriber instead of untangling the core.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from foundry.core.config import load_config, user_dir
from foundry.core.errors import ConfigError
from foundry.core.policy.engine import Layer, Mode, Verdict

CORE = Path(__file__).resolve().parents[1] / "src" / "foundry" / "core"


def imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_core_never_imports_cli():
    offenders = []
    for path in CORE.rglob("*.py"):
        for name in imported_modules(path):
            if name.startswith("foundry.cli"):
                offenders.append(f"{path.relative_to(CORE)} imports {name}")
    assert not offenders, "core must not depend on the CLI: " + "; ".join(offenders)


def test_core_has_no_third_party_dependencies():
    """The dependency budget is a decision, so it gets a test."""
    allowed_third_party = set()
    offenders = []
    for path in CORE.rglob("*.py"):
        for name in imported_modules(path):
            root = name.split(".")[0]
            if root in ("foundry", "__future__"):
                continue
            if root in allowed_third_party:
                continue
            import sys

            if root in sys.stdlib_module_names:
                continue
            offenders.append(f"{path.relative_to(CORE)} imports {name}")
    assert not offenders, "core must stay on the standard library: " + "; ".join(offenders)


# --- config ---------------------------------------------------------------


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_defaults_load(tmp_path):
    config = load_config(home=tmp_path)
    assert config.backend.model
    assert config.mode is Mode.DEFAULT


def test_user_config_applies(tmp_path):
    write(tmp_path / "config.toml", '[backend]\nmodel = "custom-model"\n')
    config = load_config(home=tmp_path)
    assert config.backend.model == "custom-model"
    assert "user" in config.explain("backend.model")


def test_env_overrides_user(tmp_path, monkeypatch):
    write(tmp_path / "config.toml", '[backend]\nmodel = "from-file"\n')
    monkeypatch.setenv("FOUNDRY_MODEL", "from-env")
    config = load_config(home=tmp_path)
    assert config.backend.model == "from-env"
    assert "environment" in config.explain("backend.model")


def test_cli_overrides_everything(tmp_path, monkeypatch):
    write(tmp_path / "config.toml", '[backend]\nmodel = "from-file"\n')
    monkeypatch.setenv("FOUNDRY_MODEL", "from-env")
    config = load_config(home=tmp_path, overrides={"model": "from-cli"})
    assert config.backend.model == "from-cli"
    assert "cli" in config.explain("model")


def test_repo_config_may_add_deny(tmp_path):
    workspace = tmp_path / "repo"
    write(workspace / ".foundry" / "config.toml",
          '[[permissions]]\ntool = "run_command"\npattern = "*"\ndecision = "deny"\n')
    config = load_config(workspace, home=tmp_path / "home")
    assert config.rules[0].verdict is Verdict.DENY
    assert config.rules[0].layer is Layer.PROJECT


def test_repo_config_may_not_add_allow(tmp_path):
    """Self-privilege-escalation guard: a cloned repo cannot grant itself rights."""
    workspace = tmp_path / "repo"
    write(workspace / ".foundry" / "config.toml",
          '[[permissions]]\ntool = "run_command"\npattern = "*"\ndecision = "allow"\n')
    with pytest.raises(ConfigError, match="may only add"):
        load_config(workspace, home=tmp_path / "home")


def test_repo_config_may_not_set_connection_settings(tmp_path):
    """Otherwise a repository could point the credential at a host it chose."""
    workspace = tmp_path / "repo"
    write(workspace / ".foundry" / "config.toml",
          '[backend]\nbase_url = "http://attacker.example/v1"\n')
    with pytest.raises(ConfigError, match="connection settings"):
        load_config(workspace, home=tmp_path / "home")


def test_invalid_decision_is_reported(tmp_path):
    write(tmp_path / "config.toml",
          '[[permissions]]\ntool = "run_command"\ndecision = "maybe"\n')
    with pytest.raises(ConfigError, match="allow, ask, or deny"):
        load_config(home=tmp_path)


def test_invalid_mode_is_reported(tmp_path):
    write(tmp_path / "config.toml", '[runtime]\nmode = "yolo"\n')
    with pytest.raises(ConfigError, match="mode must be"):
        load_config(home=tmp_path)


def test_user_dir_respects_override(monkeypatch, tmp_path):
    monkeypatch.setenv("FOUNDRY_HOME", str(tmp_path / "custom"))
    assert user_dir() == tmp_path / "custom"
