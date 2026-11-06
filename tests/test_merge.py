"""Tests for airlock.merge (Task 1.5).

Real temporary git repos + bare remotes are built inside a fake HOME (via
create_sandbox), so ~/sandboxes and this repository are never touched. Only
failure paths that are awkward to stage for real (push rejection, missing
mise) are simulated by patching subprocess.run.
"""

import io
import os
import subprocess
import sys
from pathlib import Path

import pytest

import airlock.cli as cli
import airlock.merge as merge_mod
from airlock.merge import MergeError, merge_sandbox
from airlock.sandbox import create_sandbox, parse_sandbox_name

PROJECT_NAME = "mainproj"


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    return home


@pytest.fixture
def fake_mise(tmp_path, monkeypatch):
    """Stub mise: prints a marker, honours MISE_LOG and FAKE_MISE_EXIT."""
    stub = tmp_path / "stubbin"
    stub.mkdir()
    script = stub / "mise"
    script.write_text(
        "#!/bin/sh\n"
        'echo "mise-install-stub-ok"\n'
        '[ -n "$MISE_LOG" ] && echo ran >> "$MISE_LOG"\n'
        'exit "${FAKE_MISE_EXIT:-0}"\n'
    )
    script.chmod(0o755)
    monkeypatch.setenv("PATH", str(stub) + os.pathsep + os.environ.get("PATH", ""))
    return stub


def _git(*args: str, cwd: Path) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    )
    return proc.stdout.strip()


def _commit_all(repo: Path, message: str) -> None:
    _git("add", "-A", cwd=repo)
    _git(
        "-c", "user.name=t", "-c", "user.email=t@example.com",
        "commit", "-q", "-m", message, cwd=repo,
    )


@pytest.fixture
def project_pair(fake_home, fake_mise, tmp_path):
    """Main repo (clean, with version config) plus a sandbox created from it."""
    main = tmp_path / PROJECT_NAME
    main.mkdir()
    _git("init", "-q", cwd=main)
    # Repo-local identity: git merge checks the committer identity before it
    # even attempts the merge, and the fake HOME has no global gitconfig.
    _git("config", "user.name", "t", cwd=main)
    _git("config", "user.email", "t@example.com", cwd=main)
    (main / ".tool-versions").write_text("python 3.12.0\n")
    (main / "code.py").write_text("print('v1')\n")
    _commit_all(main, "initial commit")
    sandbox = create_sandbox(main, PROJECT_NAME)
    _git("config", "user.name", "t", cwd=sandbox)
    _git("config", "user.email", "t@example.com", cwd=sandbox)
    return main, sandbox


def _bump_sandbox(sandbox: Path) -> None:
    (sandbox / ".tool-versions").write_text("python 3.15.2\n")
    (sandbox / "code.py").write_text("print('v2')\n")
    _commit_all(sandbox, "bump python and code")


def _bare(fake_home: Path) -> Path:
    return fake_home / ".local" / "share" / "airlock" / "remotes" / f"{PROJECT_NAME}.git"


def _config_section(out: str) -> str:
    start = out.index("--- version config diff")
    end = out.index("--- end version config diff")
    return out[start:end]


# --- happy paths -----------------------------------------------------------------


def test_merge_happy_path_with_real_prompt_yes(project_pair, monkeypatch, capsys):
    main, sandbox = project_pair
    _bump_sandbox(sandbox)
    monkeypatch.setattr(sys, "stdin", io.StringIO("y\n"))

    rc = merge_sandbox(sandbox.name, project_root=main)

    assert rc == 0
    assert (main / ".tool-versions").read_text() == "python 3.15.2\n"
    assert (main / "code.py").read_text() == "print('v2')\n"
    out = capsys.readouterr().out
    assert "Proceed with merge? [y/N]" in out
    assert "version config diff" in out
    assert "+python 3.15.2" in out
    assert "mise-install-stub-ok" in out  # mise install ran, output captured+printed
    branch = f"sandbox/{parse_sandbox_name(sandbox.name)[1]}"
    _git("rev-parse", "--quiet", "--verify", f"refs/heads/{branch}", cwd=_bare(
        Path(os.environ["HOME"])
    ))  # push to the bare remote really happened


def test_config_diff_shows_only_config_files(project_pair, capsys):
    main, sandbox = project_pair
    _bump_sandbox(sandbox)

    rc = merge_sandbox(sandbox.name, project_root=main, confirm=lambda: True)

    assert rc == 0
    out = capsys.readouterr().out
    section = _config_section(out)
    assert "+python 3.15.2" in section
    assert "-python 3.12.0" in section
    assert "code.py" not in section  # not even the filename
    assert "print('v2')" not in section  # and certainly not its content
    assert "code.py" in out  # but the --stat summary still lists it


@pytest.mark.parametrize(
    ("answer", "merged"),
    [("\n", False), ("n\n", False), ("y\n", True), ("yes\n", True)],
)
def test_prompt_answers(project_pair, monkeypatch, capsys, answer, merged):
    """Requirement: a bare Enter defaults to N and aborts the merge."""
    main, sandbox = project_pair
    _bump_sandbox(sandbox)
    monkeypatch.setattr(sys, "stdin", io.StringIO(answer))

    rc = merge_sandbox(sandbox.name, project_root=main)

    if merged:
        assert rc == 0
        assert (main / ".tool-versions").read_text() == "python 3.15.2\n"
    else:
        assert rc == 1
        assert (main / ".tool-versions").read_text() == "python 3.12.0\n"
        assert "Aborted" in capsys.readouterr().out


def test_nothing_to_merge_when_sandbox_has_no_commits(project_pair, capsys):
    main, sandbox = project_pair

    rc = merge_sandbox(sandbox.name, project_root=main, confirm=lambda: True)

    assert rc == 0
    out = capsys.readouterr().out
    assert "Nothing to merge" in out
    assert "[y/N]" not in out  # no prompt when there is nothing to confirm


# --- guards ------------------------------------------------------------------------


def test_dirty_sandbox_aborts(project_pair, capsys):
    main, sandbox = project_pair
    (sandbox / "code.py").write_text("dirty\n")  # not committed

    with pytest.raises(MergeError) as excinfo:
        merge_sandbox(sandbox.name, project_root=main, confirm=lambda: True)

    assert "uncommitted changes" in str(excinfo.value)
    assert "code.py" in str(excinfo.value)
    assert (main / ".tool-versions").read_text() == "python 3.12.0\n"  # untouched


def test_dirty_main_project_aborts(project_pair):
    main, sandbox = project_pair
    _bump_sandbox(sandbox)
    (main / "code.py").write_text("dirty\n")  # not committed

    with pytest.raises(MergeError) as excinfo:
        merge_sandbox(sandbox.name, project_root=main, confirm=lambda: True)

    assert "uncommitted changes" in str(excinfo.value)
    assert (main / ".tool-versions").read_text() == "python 3.12.0\n"


def test_push_failure_aborts(project_pair, monkeypatch):
    main, sandbox = project_pair
    _bump_sandbox(sandbox)
    real_run = subprocess.run

    def fake_run(cmd, **kwargs):
        if cmd[:3] == ["git", "push", "origin"]:
            return subprocess.CompletedProcess(cmd, 128, stdout="", stderr="simulated: remote rejected")
        return real_run(cmd, **kwargs)

    monkeypatch.setattr(merge_mod.subprocess, "run", fake_run)

    with pytest.raises(MergeError) as excinfo:
        merge_sandbox(sandbox.name, project_root=main, confirm=lambda: True)

    assert "simulated: remote rejected" in str(excinfo.value)
    assert (main / ".tool-versions").read_text() == "python 3.12.0\n"


def test_merge_conflict_raises_with_git_output(project_pair, monkeypatch):
    main, sandbox = project_pair
    (sandbox / "code.py").write_text("print('sandbox')\n")
    _commit_all(sandbox, "sandbox change")
    (main / "code.py").write_text("print('main')\n")
    _commit_all(main, "main change")
    # Git output is localized on some systems (КОНФЛИКТ); pin it to English.
    monkeypatch.setenv("LC_ALL", "C")

    with pytest.raises(MergeError) as excinfo:
        merge_sandbox(sandbox.name, project_root=main, confirm=lambda: True)

    assert "merge failed" in str(excinfo.value)
    assert "CONFLICT" in str(excinfo.value)


# --- input validation ----------------------------------------------------------------


def test_sandbox_not_found(project_pair):
    main, _ = project_pair

    with pytest.raises(MergeError, match="sandbox not found"):
        merge_sandbox("nosuchproj_20260101_000000", project_root=main)


def test_invalid_sandbox_name(project_pair):
    main, _ = project_pair

    with pytest.raises(MergeError, match="invalid sandbox name"):
        merge_sandbox("just-a-name", project_root=main)


def test_wrong_project_aborts(project_pair, tmp_path):
    main, sandbox = project_pair
    other = tmp_path / "otherproj"
    other.mkdir()
    _git("init", "-q", cwd=other)

    with pytest.raises(MergeError, match="belongs to project"):
        merge_sandbox(sandbox.name, project_root=other)


# --- mise integration ----------------------------------------------------------------


def test_mise_not_run_without_runtime_config_change(project_pair, monkeypatch, tmp_path):
    main, sandbox = project_pair
    mise_log = tmp_path / "mise.log"
    monkeypatch.setenv("MISE_LOG", str(mise_log))
    (sandbox / "code.py").write_text("print('v2')\n")
    _commit_all(sandbox, "code only")

    rc = merge_sandbox(sandbox.name, project_root=main, confirm=lambda: True)

    assert rc == 0
    assert (main / "code.py").read_text() == "print('v2')\n"
    assert not mise_log.exists()  # merge of code alone must not trigger mise


def test_merge_succeeds_when_mise_is_not_installed(project_pair, monkeypatch, capsys):
    """Requirement: missing mise is a warning, never a merge failure."""
    main, sandbox = project_pair
    (sandbox / ".tool-versions").write_text("python 3.15.2\n")
    _commit_all(sandbox, "bump python")
    real_run = subprocess.run

    def no_mise(cmd, **kwargs):
        if cmd and cmd[0] == "mise":
            raise FileNotFoundError("mise")
        return real_run(cmd, **kwargs)

    monkeypatch.setattr(merge_mod.subprocess, "run", no_mise)

    rc = merge_sandbox(sandbox.name, project_root=main, confirm=lambda: True)

    assert rc == 0
    assert (main / ".tool-versions").read_text() == "python 3.15.2\n"
    err = capsys.readouterr().err
    assert "mise" in err and "not found" in err


def test_mise_install_failure_warns_but_merge_succeeds(project_pair, monkeypatch, capsys):
    main, sandbox = project_pair
    (sandbox / ".tool-versions").write_text("python 3.15.2\n")
    _commit_all(sandbox, "bump python")
    monkeypatch.setenv("FAKE_MISE_EXIT", "1")  # stub mise exits 1

    rc = merge_sandbox(sandbox.name, project_root=main, confirm=lambda: True)

    assert rc == 0
    assert (main / ".tool-versions").read_text() == "python 3.15.2\n"
    err = capsys.readouterr().err
    assert "mise install" in err and "failed" in err


# --- cli wiring -----------------------------------------------------------------------


def test_cmd_merge_routes_to_merge_sandbox(monkeypatch):
    called = {}

    def fake_merge_sandbox(name):
        called["name"] = name
        return 7

    monkeypatch.setattr(cli, "merge_sandbox", fake_merge_sandbox)

    assert cli.cmd_merge("proj_20260101_000000") == 7
    assert called["name"] == "proj_20260101_000000"
