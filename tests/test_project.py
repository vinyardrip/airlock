"""Tests for airlock.project."""

import subprocess
from pathlib import Path

import pytest

from airlock.project import (
    detect_project_root,
    looks_like_container,
    _has_nonempty_gitignore,
)


def _make_git_repo(path: Path) -> None:
    """Create a minimal git repo (no commits needed)."""
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)


def test_detect_root_in_project(tmp_path):
    _make_git_repo(tmp_path)
    assert detect_project_root(tmp_path) == tmp_path.resolve()


def test_detect_root_from_subdirectory(tmp_path):
    _make_git_repo(tmp_path)
    sub = tmp_path / "airlock"
    sub.mkdir()
    assert detect_project_root(sub) == tmp_path.resolve()


def test_detect_root_none(tmp_path):
    # tmp_path has no .git anywhere up to root (pytest tmp is isolated)
    # But parents of tmp_path might have .git in CI... guard with a deep tmp.
    assert detect_project_root(tmp_path) is None or detect_project_root(tmp_path) != tmp_path


def test_detect_root_none_strict():
    # Use a path that definitely has no .git above it (filesystem root has none).
    assert detect_project_root(Path("/")) is None


def test_gitignore_nonempty(tmp_path):
    (tmp_path / ".gitignore").write_text("# comment\n\nnode_modules/\n")
    assert _has_nonempty_gitignore(tmp_path) is True


def test_gitignore_only_comments(tmp_path):
    (tmp_path / ".gitignore").write_text("# comment\n\n   \n")
    assert _has_nonempty_gitignore(tmp_path) is False


def test_gitignore_missing(tmp_path):
    assert _has_nonempty_gitignore(tmp_path) is False


def test_project_with_gitignore_not_container(tmp_path):
    _make_git_repo(tmp_path)
    (tmp_path / ".gitignore").write_text("node_modules/\n")
    # Even with nested .git, .gitignore marks it as a project.
    nested = tmp_path / "nested"
    nested.mkdir()
    _make_git_repo(nested)

    is_container, details = looks_like_container(tmp_path)
    assert is_container is False
    assert details["has_gitignore"] is True


def test_container_without_gitignore(tmp_path):
    _make_git_repo(tmp_path)
    for name in ("alpha", "beta", "gamma"):
        sub = tmp_path / name
        sub.mkdir()
        _make_git_repo(sub)

    is_container, details = looks_like_container(tmp_path)
    assert is_container is True
    assert "alpha" in details["nested_projects"]
    assert details["has_gitignore"] is False
    assert details["reasons"]


def test_plain_project_not_container(tmp_path):
    _make_git_repo(tmp_path)
    # No .gitignore, no nested .git, tiny size → not a container (small, no signals).
    is_container, details = looks_like_container(tmp_path)
    assert is_container is False
    assert details["reasons"] == []