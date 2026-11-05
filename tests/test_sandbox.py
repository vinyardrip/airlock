"""Tests for airlock.sandbox.

The fake HOME is isolated via monkeypatch.setenv("HOME", ...) so sandboxes and
bare remotes never touch the real user home. Subshell entry is tested manually
in Task 1.3 verification.
"""

import os
import subprocess
from pathlib import Path

import pytest

import airlock.sandbox as sandbox_mod
from airlock.sandbox import (
    SandboxError,
    bare_remote_path,
    create_sandbox,
    new_sandbox_id,
    sandbox_root,
)

PROJECT_NAME = "fakeproj"


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    return home


@pytest.fixture
def fake_mise(tmp_path, monkeypatch):
    """Stub mise so tests never invoke the real installer."""
    stub = tmp_path / "stubbin"
    stub.mkdir()
    script = stub / "mise"
    script.write_text("#!/bin/sh\nexit 0\n")
    script.chmod(0o755)
    monkeypatch.setenv("PATH", str(stub) + os.pathsep + os.environ.get("PATH", ""))
    return stub


def _init_project(path: Path) -> Path:
    """Create a fake git project with .tool-versions and one commit."""
    path.mkdir(parents=True, exist_ok=True)
    (path / ".tool-versions").write_text("python 3.15.2\n")
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "add", "-A"], cwd=path, check=True)
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com",
         "commit", "-q", "-m", "initial commit"],
        cwd=path,
        check=True,
    )
    return path


def _git(*args: str, cwd: Path) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    )
    return proc.stdout.strip()


def test_sandbox_dir_created(fake_home, fake_mise, tmp_path):
    project = _init_project(tmp_path / PROJECT_NAME)

    path = create_sandbox(project, PROJECT_NAME)

    assert path.parent == fake_home / "sandboxes"
    assert (fake_home / "sandboxes").is_dir()
    assert path.name.startswith(f"{PROJECT_NAME}_")
    assert path.is_dir()
    assert (path / ".tool-versions").exists()


def test_sandbox_git_log_shows_project_commits(fake_home, fake_mise, tmp_path):
    project = _init_project(tmp_path / PROJECT_NAME)
    (project / "second.txt").write_text("2\n")
    _git("add", "-A", cwd=project)
    _git("-c", "user.name=t", "-c", "user.email=t@example.com",
         "commit", "-q", "-m", "second commit", cwd=project)

    sandbox = create_sandbox(project, PROJECT_NAME)

    assert (sandbox / ".git").exists()
    log = _git("log", "--oneline", cwd=sandbox)
    assert "initial commit" in log
    assert "second commit" in log


def test_sandbox_remotes_configured(fake_home, fake_mise, tmp_path):
    project = _init_project(tmp_path / PROJECT_NAME)
    _git("remote", "add", "origin", "https://github.com/example/proj.git", cwd=project)

    sandbox = create_sandbox(project, PROJECT_NAME)
    bare = bare_remote_path(PROJECT_NAME)

    assert sandbox is not None
    assert _git("config", "--get", "remote.origin.url", cwd=sandbox) == str(bare)
    assert _git("config", "--get", "remote.upstream.url", cwd=sandbox) == (
        "https://github.com/example/proj.git"
    )
    assert _git("config", "--get", "remote.upstream.pushurl", cwd=sandbox) == (
        f"no_push://{PROJECT_NAME}"
    )


def test_sandbox_branch_named_after_id(fake_home, fake_mise, tmp_path):
    project = _init_project(tmp_path / PROJECT_NAME)

    sandbox = create_sandbox(project, PROJECT_NAME)

    sandbox_id = sandbox.name[len(PROJECT_NAME) + 1:]
    expected = f"sandbox/{sandbox_id}"
    assert _git("branch", "--show-current", cwd=sandbox) == expected
    assert len(sandbox_id.split("_")) == 2


def test_bare_remote_is_bare(fake_home, fake_mise, tmp_path):
    project = _init_project(tmp_path / PROJECT_NAME)

    create_sandbox(project, PROJECT_NAME)

    bare = bare_remote_path(PROJECT_NAME)
    assert _git("rev-parse", "--is-bare-repository", cwd=bare) == "true"


def test_main_project_unchanged(fake_home, fake_mise, tmp_path):
    project = _init_project(tmp_path / PROJECT_NAME)
    before = _git("branch", "--show-current", cwd=project)

    create_sandbox(project, PROJECT_NAME)

    assert _git("status", "--porcelain", cwd=project) == ""
    assert _git("branch", "--show-current", cwd=project) == before


def test_same_second_sandboxes_are_unique(fake_home, fake_mise, tmp_path, monkeypatch):
    """Task 1.3 test 7: two runs in one second must not overwrite each other."""
    monkeypatch.setattr(sandbox_mod, "new_sandbox_id", lambda: "20260101_000000")
    project = _init_project(tmp_path / PROJECT_NAME)

    first = create_sandbox(project, PROJECT_NAME)
    second = create_sandbox(project, PROJECT_NAME)

    assert first != second
    assert first.exists() and second.exists()
    assert second.name == first.name + "_2"
    # Same-second collision keeps the original branch id, not the counter.
    assert _git("branch", "--show-current", cwd=second) == "sandbox/20260101_000000"


def test_sandbox_error_on_command_failure(fake_home, tmp_path, monkeypatch):
    project = _init_project(tmp_path / PROJECT_NAME)

    def fake_run(*args, **kwargs):
        raise subprocess.CalledProcessError(
            returncode=128, cmd=["git", "clone"], stderr="boom: simulated failure"
        )

    monkeypatch.setattr(sandbox_mod.subprocess, "run", fake_run)

    with pytest.raises(SandboxError) as excinfo:
        create_sandbox(project, PROJECT_NAME)

    assert "boom: simulated failure" in str(excinfo.value)
    assert "git init --bare" in str(excinfo.value)
