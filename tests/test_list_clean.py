"""Tests for airlock.registry (Task 1.6: list + clean).

Sandboxes are real git clones built via create_sandbox inside a fake HOME, so
~/sandboxes/ resolves to tmp_path and the real directory is never touched.
"""

import io
import os
import subprocess
import sys
from pathlib import Path

import pytest

import airlock.cli as cli
import airlock.registry as registry_mod
from airlock.registry import (
    CleanError,
    clean_sandbox,
    format_sandbox_table,
    list_sandboxes,
)
from airlock.sandbox import create_sandbox, parse_sandbox_name

PROJECT_NAME = "listproj"


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    return home


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
def project_with_sandbox(fake_home, tmp_path):
    """Main repo + one fresh sandbox (clean tree, branch not yet pushed)."""
    main = tmp_path / PROJECT_NAME
    main.mkdir()
    _git("init", "-q", cwd=main)
    (main / "code.py").write_text("print('v1')\n")
    _commit_all(main, "initial commit")
    sandbox = create_sandbox(main, PROJECT_NAME)
    return main, sandbox


def _push_branch(sandbox: Path) -> None:
    branch = f"sandbox/{parse_sandbox_name(sandbox.name)[1]}"
    _git("push", "origin", branch, cwd=sandbox)


# --- list -------------------------------------------------------------------------


def test_list_shows_clean_sandbox(project_with_sandbox):
    _, sandbox = project_with_sandbox

    infos, skipped = list_sandboxes()

    assert skipped == 0
    assert len(infos) == 1
    info = infos[0]
    assert info.name == sandbox.name
    assert info.project == PROJECT_NAME
    assert info.id == sandbox.name[len(PROJECT_NAME) + 1 :]
    assert info.branch == f"sandbox/{info.id}"
    assert info.status == "[clean]"  # fresh clone has a clean tree


def test_list_marks_dirty_sandboxes(project_with_sandbox):
    _, sandbox = project_with_sandbox
    (sandbox / "code.py").write_text("dirty\n")

    infos, _ = list_sandboxes()

    assert infos[0].status == "[dirty]"


def test_list_sorts_by_project_and_skips_foreign_entries(project_with_sandbox, fake_home):
    _, sandbox = project_with_sandbox
    # A second project sorts before the first; foreign entries are skipped.
    second_main = fake_home.parent / "aaaproj"
    second_main.mkdir()
    _git("init", "-q", cwd=second_main)
    (second_main / "f.txt").write_text("x\n")
    _commit_all(second_main, "initial commit")
    create_sandbox(second_main, "aaaproj")
    (fake_home / "sandboxes" / "not_a_sandbox").mkdir()
    (fake_home / "sandboxes" / "stray.txt").write_text("junk\n")

    infos, skipped = list_sandboxes()

    assert skipped == 2
    assert [info.project for info in infos] == ["aaaproj", PROJECT_NAME]


def test_format_table_headers_and_alignment(project_with_sandbox):
    _, sandbox = project_with_sandbox
    infos, _ = list_sandboxes()

    lines = format_sandbox_table(infos).splitlines()

    assert lines[0].split() == ["Project", "ID", "Branch", "Status"]
    assert set(lines[1]) <= {"-", " "} and "---" in lines[1]
    # Every row's columns start at the same offsets as the header's.
    header_cols = [lines[0].index(h) for h in ("Project", "ID", "Branch", "Status")]
    info = infos[0]
    row_cols = [
        lines[2].index(c) for c in (info.project, info.id, info.branch, info.status)
    ]
    assert row_cols == header_cols


def test_format_table_with_no_rows():
    table = format_sandbox_table([])

    assert table.splitlines()[0].split() == ["Project", "ID", "Branch", "Status"]


def test_cmd_list_prints_table(project_with_sandbox, capsys):
    rc = cli.cmd_list()

    assert rc == 0
    out = capsys.readouterr().out
    assert "Project" in out and "ID" in out and "Branch" in out and "Status" in out
    assert "[clean]" in out


def test_cmd_list_empty(fake_home, capsys):
    assert cli.cmd_list() == 0
    assert "No sandboxes" in capsys.readouterr().out


# --- clean: strict checks ----------------------------------------------------------


def test_clean_refuses_dirty_sandbox(project_with_sandbox):
    _, sandbox = project_with_sandbox
    (sandbox / "code.py").write_text("dirty\n")

    with pytest.raises(CleanError) as excinfo:
        clean_sandbox(sandbox.name)

    assert "uncommitted changes" in str(excinfo.value)
    assert "code.py" in str(excinfo.value)
    assert sandbox.is_dir()  # nothing deleted


def test_clean_refuses_unpushed_branch(project_with_sandbox):
    """A fresh sandbox never had its branch pushed: deletion must be refused."""
    _, sandbox = project_with_sandbox

    with pytest.raises(CleanError) as excinfo:
        clean_sandbox(sandbox.name)

    assert "not fully pushed" in str(excinfo.value)
    assert "unpushed commit" in str(excinfo.value)
    assert sandbox.is_dir()


def test_clean_refuses_after_local_commit(project_with_sandbox):
    _, sandbox = project_with_sandbox
    _push_branch(sandbox)
    (sandbox / "code.py").write_text("print('v2')\n")
    _commit_all(sandbox, "local work")

    with pytest.raises(CleanError) as excinfo:
        clean_sandbox(sandbox.name)

    assert "1 unpushed commit" in str(excinfo.value)
    assert sandbox.is_dir()


def test_clean_accepts_pushed_clean_sandbox(project_with_sandbox, monkeypatch):
    _, sandbox = project_with_sandbox
    _push_branch(sandbox)
    monkeypatch.setattr(registry_mod, "_default_clean_confirm", lambda p: True)

    rc = clean_sandbox(sandbox.name)

    assert rc == 0
    assert not sandbox.exists()


def test_clean_confirmation_typed_yes_only(project_with_sandbox, monkeypatch):
    """--force still requires literally typing 'yes' (case/whitespace tolerated)."""
    _, sandbox = project_with_sandbox
    _push_branch(sandbox)
    (sandbox / "code.py").write_text("dirty\n")  # checks bypassed by --force

    # Anything but 'yes' aborts and keeps the sandbox.
    for answer in ("y\n", "\n", "no\n"):
        monkeypatch.setattr(sys, "stdin", io.StringIO(answer))
        assert clean_sandbox(sandbox.name, force=True) == 1
        assert sandbox.is_dir()

    # Exactly 'yes' deletes.
    monkeypatch.setattr(sys, "stdin", io.StringIO("  YES \n"))
    assert clean_sandbox(sandbox.name, force=True) == 0
    assert not sandbox.exists()


def test_clean_abort_keeps_sandbox(project_with_sandbox, monkeypatch, capsys):
    _, sandbox = project_with_sandbox
    _push_branch(sandbox)
    monkeypatch.setattr(sys, "stdin", io.StringIO("\n"))

    rc = clean_sandbox(sandbox.name)

    assert rc == 1
    assert sandbox.is_dir()
    assert "Aborted" in capsys.readouterr().out


# --- clean: input validation --------------------------------------------------------


def test_clean_sandbox_not_found(fake_home):
    with pytest.raises(CleanError, match="sandbox not found"):
        clean_sandbox("nosuchproj_20260101_000000")


def test_clean_invalid_name(fake_home):
    with pytest.raises(CleanError, match="invalid sandbox name"):
        clean_sandbox("just-a-name")


# --- cli wiring ----------------------------------------------------------------------


def test_cmd_clean_routes_and_reports_errors(monkeypatch, capsys):
    from airlock.registry import SandboxInfo

    dummy = SandboxInfo(
        "proj_20260101_000000", "proj", "20260101_000000", "sandbox/20260101_000000", "[clean]"
    )
    monkeypatch.setattr(cli, "resolve_sandbox", lambda arg, **kw: dummy)
    calls = {}

    def fake_clean(name, force=False):
        calls["args"] = (name, force)
        if force:
            return 0
        raise CleanError("refused: simulated")

    monkeypatch.setattr(cli, "clean_sandbox", fake_clean)

    assert cli.cmd_clean("proj_20260101_000000") == 1
    assert "refused: simulated" in capsys.readouterr().err

    assert cli.cmd_clean("proj_20260101_000000", force=True) == 0
    assert calls["args"] == ("proj_20260101_000000", True)
