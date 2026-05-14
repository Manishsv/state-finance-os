"""Tests for the CLI's dotenv loader, especially the empty-string env-var fix.

The bug being regressed: `load_dotenv(override=False)` treats `KEY=""` as
"already set" and refuses to populate from .env. We strip empty values from
os.environ for keys defined in the dotenv file before loading.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from financeos.network.cli.main import _clear_empty_dotenv_keys


def _write_env(path: Path, lines: list) -> None:
    path.write_text("\n".join(lines) + "\n")


def test_empty_env_var_for_dotenv_key_is_cleared(tmp_path):
    env = tmp_path / ".env"
    _write_env(env, ["FOO=real_value", "BAR=other"])
    environ = {"FOO": "", "BAR": "shell_set", "UNRELATED": ""}

    cleared = _clear_empty_dotenv_keys(env, environ)

    assert cleared == ["FOO"]
    assert "FOO" not in environ
    # BAR is non-empty in environ — must be preserved (override=False semantics)
    assert environ["BAR"] == "shell_set"
    # UNRELATED isn't in the dotenv file — must not be touched
    assert environ["UNRELATED"] == ""


def test_no_op_when_environ_has_no_empty_matching_keys(tmp_path):
    env = tmp_path / ".env"
    _write_env(env, ["FOO=real"])
    environ = {"BAR": "x"}
    cleared = _clear_empty_dotenv_keys(env, environ)
    assert cleared == []
    assert environ == {"BAR": "x"}


def test_clearing_makes_dotenv_load_actually_populate(tmp_path, monkeypatch):
    """End-to-end: empty env var + clear + load_dotenv = .env value wins."""
    from dotenv import load_dotenv

    env = tmp_path / ".env"
    _write_env(env, ["MY_TEST_VAR=from_dotenv"])

    monkeypatch.setenv("MY_TEST_VAR", "")
    # Without the clear, override=False would skip — verify that's the bug
    load_dotenv(dotenv_path=env, override=False)
    import os
    assert os.environ.get("MY_TEST_VAR") == "", "regression: dotenv should be skipping"

    # Now apply the fix
    _clear_empty_dotenv_keys(env, os.environ)
    load_dotenv(dotenv_path=env, override=False)
    assert os.environ.get("MY_TEST_VAR") == "from_dotenv"
