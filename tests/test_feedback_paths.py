"""Tests for feedback log + embedding DB path resolution.

Verifies the project-local default (data/_feedback/) and that env vars
and config overrides take precedence in the documented order.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from ocr_router.cli import (
    _default_feedback_dir,
    _feedback_log_path,
    _resolve_embed_db_path,
    _resolve_feedback_path,
)


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    """Each test starts with no relevant env overrides."""
    monkeypatch.delenv("OCR_FEEDBACK_DIR", raising=False)
    monkeypatch.delenv("OCR_FEEDBACK_LOG", raising=False)
    monkeypatch.delenv("OCR_EMBEDDINGS_DB", raising=False)


# ---------------------------------------------------------------------------
# _default_feedback_dir
# ---------------------------------------------------------------------------

def test_default_feedback_dir_is_project_local(monkeypatch, tmp_path):
    """Default is `<cwd>/data/_feedback`, NOT next to the Documents tree."""
    monkeypatch.chdir(tmp_path)
    assert _default_feedback_dir() == tmp_path / "data" / "_feedback"


def test_default_feedback_dir_respects_env(monkeypatch, tmp_path):
    custom = tmp_path / "my-feedback-stash"
    monkeypatch.setenv("OCR_FEEDBACK_DIR", str(custom))
    assert _default_feedback_dir() == custom


# ---------------------------------------------------------------------------
# _feedback_log_path (process()'s helper)
# ---------------------------------------------------------------------------

def test_feedback_log_default_is_project_local(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    # output_dir intentionally passed but should NOT be used as the default
    docs = tmp_path / "Documents"
    docs.mkdir()
    p = _feedback_log_path(docs, config={})
    assert p == tmp_path / "data" / "_feedback" / "corrections.jsonl"
    # Not under Documents
    assert "Documents" not in p.parts


def test_feedback_log_env_var_overrides_default(monkeypatch, tmp_path):
    custom = tmp_path / "weird" / "log.jsonl"
    monkeypatch.setenv("OCR_FEEDBACK_LOG", str(custom))
    assert _feedback_log_path(tmp_path, config={}) == custom


def test_feedback_log_config_overrides_default(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    custom = tmp_path / "via-config" / "log.jsonl"
    cfg = {"feedback": {"path": str(custom)}}
    assert _feedback_log_path(tmp_path, config=cfg) == custom


def test_feedback_log_env_var_beats_config(monkeypatch, tmp_path):
    env_path = tmp_path / "env" / "log.jsonl"
    cfg_path = tmp_path / "cfg" / "log.jsonl"
    monkeypatch.setenv("OCR_FEEDBACK_LOG", str(env_path))
    cfg = {"feedback": {"path": str(cfg_path)}}
    assert _feedback_log_path(tmp_path, config=cfg) == env_path


# ---------------------------------------------------------------------------
# _resolve_feedback_path (subcommand helper) — same precedence
# ---------------------------------------------------------------------------

def test_resolve_feedback_default_is_project_local(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    p = _resolve_feedback_path(output=None, config=None)
    assert p == tmp_path / "data" / "_feedback" / "corrections.jsonl"


def test_resolve_feedback_default_ignores_output(monkeypatch, tmp_path):
    """Even when --output is passed, the default still stays project-local."""
    monkeypatch.chdir(tmp_path)
    docs = tmp_path / "Documents"
    p = _resolve_feedback_path(output=str(docs), config=None)
    assert p == tmp_path / "data" / "_feedback" / "corrections.jsonl"


def test_resolve_feedback_env_var_wins(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    custom = tmp_path / "env-place" / "log.jsonl"
    monkeypatch.setenv("OCR_FEEDBACK_LOG", str(custom))
    assert _resolve_feedback_path(output=None, config=None) == custom


# ---------------------------------------------------------------------------
# _resolve_embed_db_path
# ---------------------------------------------------------------------------

def test_embed_db_default_is_sibling_of_log(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    db = _resolve_embed_db_path(output=None, config=None)
    assert db == tmp_path / "data" / "_feedback" / "examples.sqlite"


def test_embed_db_env_var_wins(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    custom = tmp_path / "store.db"
    monkeypatch.setenv("OCR_EMBEDDINGS_DB", str(custom))
    assert _resolve_embed_db_path(output=None, config=None) == custom


def test_embed_db_inherits_feedback_log_relocation(monkeypatch, tmp_path):
    """When OCR_FEEDBACK_LOG moves the log, the DB follows it as a sibling."""
    monkeypatch.chdir(tmp_path)
    moved_log = tmp_path / "elsewhere" / "log.jsonl"
    monkeypatch.setenv("OCR_FEEDBACK_LOG", str(moved_log))
    db = _resolve_embed_db_path(output=None, config=None)
    assert db == tmp_path / "elsewhere" / "examples.sqlite"
