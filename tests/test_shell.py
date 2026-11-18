"""Tests for the sandbox shell indicator (ZDOTDIR shim + AIRLOCK_* env)."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

import airlock.cli as cli
from airlock import __version__
from airlock.shell import (
    SHIM_TEMPLATE,
    base_info,
    is_zsh,
    sandbox_zdotdir,
    zsh_shim_dir,
)

ZSH = shutil.which("zsh")


def _zsh_required():
    return pytest.mark.skipif(ZSH is None, reason="zsh is not installed")


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout.strip()


def _make_repo(path: Path, branch: str = "main") -> Path:
    path.mkdir(parents=True)
    _git(path, "init", "-q", "-b", branch)
    _git(path, "config", "user.email", "airlock@example.test")
    _git(path, "config", "user.name", "airlock test")
    (path / "f.txt").write_text("one\n")
    _git(path, "add", "f.txt")
    _git(path, "commit", "-qm", "base")
    return path


# --- env + ZDOTDIR wiring ------------------------------------------------


def test_cmd_enter_exports_env_and_zsh_shim(tmp_path, monkeypatch, capsys):
    project = tmp_path / "fakeproj"
    project.mkdir()
    (project / ".git").mkdir()
    fake_sandbox = tmp_path / "fakeproj_20260101_000000"
    fake_sandbox.mkdir()

    monkeypatch.setattr(Path, "cwd", lambda: project)
    monkeypatch.setattr(cli, "create_sandbox", lambda root, name: fake_sandbox)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("SHELL", ZSH or "/bin/zsh")

    seen = {}

    def fake_run(*args, **kwargs):
        env = kwargs["env"]
        seen["env"] = dict(env)
        seen["cwd"] = kwargs["cwd"]
        seen["shim_exists"] = (Path(env["ZDOTDIR"]) / ".zshrc").is_file()
        return subprocess.CompletedProcess(args[0], 0)

    monkeypatch.setattr(cli.subprocess, "run", fake_run)

    assert cli.cmd_enter(None) == 0
    assert seen["cwd"] == fake_sandbox
    assert seen["shim_exists"] is True

    env = seen["env"]
    assert env["AIRLOCK_ACTIVE"] == "1"
    assert env["AIRLOCK_MAIN"] == str(project.resolve())
    assert env["AIRLOCK_SANDBOX"] == "20260101_000000"
    assert env["AIRLOCK_VERSION"] == __version__
    assert env["AIRLOCK_BASE_SHA"] == ""
    assert env["AIRLOCK_BASE_REF"] == "HEAD"
    assert env["ZDOTDIR"] == str(zsh_shim_dir(fake_sandbox))
    # The generated shim is removed once the subshell returns.
    assert not zsh_shim_dir(fake_sandbox).exists()
    assert "[i] Exited sandbox." in capsys.readouterr().out


def test_zdotdir_is_restored_after_subshell(tmp_path, monkeypatch):
    sandbox = tmp_path / "proj_20260101_000000"
    sandbox.mkdir()
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))

    env = {"SHELL": ZSH or "/bin/zsh", "ZDOTDIR": "/original/zdotdir"}
    with sandbox_zdotdir(sandbox, env):
        assert env["ZDOTDIR"] == str(zsh_shim_dir(sandbox))
        assert (Path(env["ZDOTDIR"]) / ".zshrc").is_file()
    assert env["ZDOTDIR"] == "/original/zdotdir"
    assert not zsh_shim_dir(sandbox).exists()

    env = {"SHELL": ZSH or "/bin/zsh"}
    with sandbox_zdotdir(sandbox, env):
        assert "ZDOTDIR" in env
    assert "ZDOTDIR" not in env
    assert not zsh_shim_dir(sandbox).exists()


def test_non_zsh_shell_is_left_alone(tmp_path, monkeypatch):
    sandbox = tmp_path / "proj_20260101_000000"
    sandbox.mkdir()
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    env = {"SHELL": "/bin/bash", "ZDOTDIR": "/my/zdotdir"}

    with sandbox_zdotdir(sandbox, env):
        assert env["ZDOTDIR"] == "/my/zdotdir"
        assert not zsh_shim_dir(sandbox).exists()

    assert is_zsh("/usr/bin/zsh-5.9") is True
    assert is_zsh("/bin/bash") is False


def test_base_info_reads_main_copy(tmp_path):
    main = _make_repo(tmp_path / "main")
    sha, ref = base_info(main)
    assert sha == _git(main, "rev-parse", "--short", "HEAD")
    assert ref == "main"


# --- template contract ---------------------------------------------------


def test_template_contract():
    text = SHIM_TEMPLATE.read_text(encoding="utf-8")
    for needle in (
        "add-zsh-hook precmd _airlock_precmd",
        "_AIRLOCK_BASE_PROMPT",
        "%B%K{196}%F{231} sb:",
        "${AIRLOCK_SANDBOX}",
        "${AIRLOCK_VERSION}",
        "${sha}${marker}${base}",
        "git rev-parse --short HEAD",
        "git status --porcelain",
        "HEAD..$AIRLOCK_BASE_SHA",
        r"\033]2;airlock:%s@%s\007",
    ):
        assert needle in text, needle
    assert "source ~/.zshrc" not in text
    assert "source $HOME/.zshrc" not in text
    assert r"\033]11" not in text
    assert "eval \"$(mise activate zsh" in text


# --- real zsh integration ------------------------------------------------


def _run_precmd(cwd: Path, env: dict[str, str]) -> str:
    script = f'source {SHIM_TEMPLATE}; _airlock_precmd; print -r -- "$PROMPT"'
    proc = subprocess.run(
        [ZSH, "-f", "-c", script],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


@_zsh_required()
def test_segment_updates_sha_marker_and_base_ahead(tmp_path, monkeypatch):
    main = _make_repo(tmp_path / "main")
    sandbox = tmp_path / "sandbox"
    _git(tmp_path, "clone", "--local", str(main), str(sandbox))
    _git(sandbox, "config", "user.email", "airlock@example.test")
    _git(sandbox, "config", "user.name", "airlock test")
    base_sha = _git(main, "rev-parse", "--short", "HEAD")

    env = os.environ.copy()
    env.update(
        {
            "HOME": str(tmp_path),
            "XDG_CACHE_HOME": str(tmp_path / "cache"),
            "AIRLOCK_ACTIVE": "1",
            "AIRLOCK_MAIN": str(main),
            "AIRLOCK_SANDBOX": "20261118_131233",
            "AIRLOCK_BASE_SHA": base_sha,
            "AIRLOCK_BASE_REF": "main",
            "AIRLOCK_VERSION": "1.0.0",
            "PS1": "draft> ",
        }
    )

    out = _run_precmd(sandbox, env)
    assert f"sb:20261118_131233 v1.0.0 {base_sha} %k%f%b" in out
    assert f"\x1b]2;airlock:20261118_131233@{base_sha}\x07" in out

    (sandbox / "f.txt").write_text("dirty\n")
    out = _run_precmd(sandbox, env)
    assert f"{base_sha}* %k%f%b" in out

    _git(sandbox, "add", "f.txt")
    _git(sandbox, "commit", "-qm", "sandbox commit")
    new_sha = _git(sandbox, "rev-parse", "--short", "HEAD")
    out = _run_precmd(sandbox, env)
    assert f"sb:20261118_131233 v1.0.0 {new_sha} %k%f%b" in out
    assert f"{new_sha}* %k%f%b" not in out

    (main / "f.txt").write_text("main moved\n")
    _git(main, "add", "f.txt")
    _git(main, "commit", "-qm", "advance")
    out = _run_precmd(sandbox, env)
    assert f"{new_sha} base+1 %k%f%b" in out


@_zsh_required()
def test_nested_zsh_shows_exactly_one_segment(tmp_path):
    main = _make_repo(tmp_path / "main")
    sandbox = tmp_path / "sandbox"
    _git(tmp_path, "clone", "--local", str(main), str(sandbox))

    zdotdir = tmp_path / "zdotdir"
    zdotdir.mkdir()
    (zdotdir / ".zshrc").write_text(SHIM_TEMPLATE.read_text(encoding="utf-8"))
    nested = tmp_path / "nested.zsh"
    nested.write_text('print -r -- "NESTED:$PROMPT"\nexit\n')

    env = os.environ.copy()
    env.update(
        {
            "HOME": str(tmp_path),
            "XDG_CACHE_HOME": str(tmp_path / "cache"),
            "ZDOTDIR": str(zdotdir),
            "AIRLOCK_ACTIVE": "1",
            "AIRLOCK_MAIN": str(main),
            "AIRLOCK_SANDBOX": "20261118_131233",
            "AIRLOCK_BASE_SHA": _git(main, "rev-parse", "--short", "HEAD"),
            "AIRLOCK_BASE_REF": "main",
            "AIRLOCK_VERSION": "1.0.0",
            "PS1": "draft> ",
        }
    )

    proc = subprocess.run(
        [ZSH, "-i"],
        cwd=sandbox,
        env=env,
        stdin=nested.open(),
        capture_output=True,
        text=True,
        check=False,
    )
    line = next(line for line in proc.stdout.splitlines() if "NESTED:" in line)
    assert "sb:20261118_131233 v1.0.0" in line
    assert line.count("%K{196}") == 1
