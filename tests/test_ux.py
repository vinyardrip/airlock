"""Tests for smart sandbox selection (merge/clean without full ids).

Real git clones via create_sandbox inside a fake HOME; two same-second
sandboxes are produced by pinning new_sandbox_id. Interactive prompts are
exercised through injected input functions or a piped sys.stdin — the real
stdin is never blocked on.
"""

import io
import os
import subprocess
import sys
from pathlib import Path

import pytest

import airlock.cli as cli
import airlock.sandbox as sandbox_mod
from airlock.registry import SandboxInfo, resolve_sandbox
from airlock.sandbox import create_sandbox

PROJECT_NAME = "uxproj"
FIRST_ID = "20261107_003629"
SECOND_ID = "20261107_010000"


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
def main_repo(tmp_path):
    main = tmp_path / PROJECT_NAME
    main.mkdir()
    _git("init", "-q", cwd=main)
    (main / "code.py").write_text("print('v1')\n")
    _commit_all(main, "initial commit")
    return main


@pytest.fixture
def two_sandboxes(main_repo, monkeypatch):
    monkeypatch.setattr(sandbox_mod, "new_sandbox_id", lambda: FIRST_ID)
    first = create_sandbox(main_repo, PROJECT_NAME)
    monkeypatch.setattr(sandbox_mod, "new_sandbox_id", lambda: SECOND_ID)
    second = create_sandbox(main_repo, PROJECT_NAME)
    return first, second


def fake_input(*answers):
    """Input double returning canned answers and recording the prompts."""
    pending = list(answers)
    calls = []

    def _input(prompt=""):
        calls.append(prompt)
        if not pending:
            raise AssertionError("unexpected extra prompt")
        return pending.pop(0)

    _input.calls = calls
    return _input


# --- omitted argument ---------------------------------------------------------------


def test_no_arg_auto_selects_single_sandbox(fake_home, main_repo, capsys):
    sandbox = create_sandbox(main_repo, PROJECT_NAME)

    selected = resolve_sandbox(None, current_project=PROJECT_NAME, home=fake_home)

    assert selected is not None and selected.name == sandbox.name
    assert f"[i] Auto-selected sandbox: {sandbox.name}" in capsys.readouterr().out


def test_no_arg_multiple_lists_newest_first_and_defaults_to_it(
    fake_home, main_repo, two_sandboxes, capsys
):
    first, second = two_sandboxes  # first is older, second is newer
    chooser = fake_input("")

    selected = resolve_sandbox(None, current_project=PROJECT_NAME, home=fake_home, input_fn=chooser)

    assert selected is not None and selected.name == second.name
    out = capsys.readouterr().out
    sha = _git("rev-parse", "--short", "HEAD", cwd=main_repo)
    assert "[i] Multiple sandboxes for 'uxproj':" in out
    assert f"  1) {second.name} [clean]  -  {sha} initial commit (2026-11-07)" in out
    assert f"  2) {first.name} [clean]  -  {sha} initial commit (2026-11-07)" in out
    assert out.index(second.name) < out.index(first.name)
    assert chooser.calls == ["Select sandbox [1-2] (default 1): "]


def test_no_arg_multiple_explicit_choice_of_newest_needs_no_confirm(
    fake_home, main_repo, two_sandboxes, capsys
):
    _, second = two_sandboxes
    chooser = fake_input("1")

    selected = resolve_sandbox(
        None, current_project=PROJECT_NAME, home=fake_home, input_fn=chooser
    )

    assert selected is not None and selected.name == second.name
    assert len(chooser.calls) == 1
    assert "Warning: You selected an older sandbox" not in capsys.readouterr().out


def test_older_choice_warns_and_confirms(fake_home, main_repo, two_sandboxes, capsys):
    first, _ = two_sandboxes
    chooser = fake_input("2", "y")

    selected = resolve_sandbox(
        None, current_project=PROJECT_NAME, home=fake_home, input_fn=chooser
    )

    assert selected is not None and selected.name == first.name
    assert len(chooser.calls) == 2
    assert "Warning: You selected an older sandbox" in chooser.calls[1]
    assert "Are you sure? [y/N]" in chooser.calls[1]


def test_older_choice_declined_aborts(fake_home, main_repo, two_sandboxes, capsys):
    chooser = fake_input("2", "n")

    selected = resolve_sandbox(
        None, current_project=PROJECT_NAME, home=fake_home, input_fn=chooser
    )

    assert selected is None
    assert "Aborted; older sandbox not selected" in capsys.readouterr().out


def test_no_arg_eof_aborts(fake_home, main_repo, two_sandboxes, capsys):
    def eof_input(prompt=""):
        raise EOFError

    selected = resolve_sandbox(
        None, current_project=PROJECT_NAME, home=fake_home, input_fn=eof_input
    )

    assert selected is None
    assert "[i] Aborted." in capsys.readouterr().out


def test_no_arg_ctrl_c_aborts(fake_home, main_repo, two_sandboxes, capsys):
    def interrupted_input(prompt=""):
        raise KeyboardInterrupt

    selected = resolve_sandbox(
        None, current_project=PROJECT_NAME, home=fake_home, input_fn=interrupted_input
    )

    assert selected is None
    assert "[i] Aborted." in capsys.readouterr().out


def test_no_arg_invalid_input_reprompts(fake_home, main_repo, two_sandboxes, capsys):
    chooser = fake_input("abc", "9", "2", "y")

    selected = resolve_sandbox(
        None, current_project=PROJECT_NAME, home=fake_home, input_fn=chooser
    )

    assert selected is not None and selected.name == f"uxproj_{FIRST_ID}"
    assert len(chooser.calls) == 4
    err = capsys.readouterr().err
    assert "invalid selection: 'abc'" in err
    assert "invalid selection: '9'" in err


def test_no_arg_without_project_errors(fake_home, two_sandboxes, capsys):
    assert resolve_sandbox(None, current_project=None, home=fake_home) is None
    assert "cannot detect the current project" in capsys.readouterr().err


def test_no_arg_project_without_sandboxes_errors(fake_home, main_repo, two_sandboxes, capsys):
    assert resolve_sandbox(None, current_project="otherproj", home=fake_home) is None
    assert "no sandboxes found for project 'otherproj'" in capsys.readouterr().err


# --- HEAD context in the picker ------------------------------------------------------


def test_picker_shows_head_hash_tag_and_subject(fake_home, main_repo, two_sandboxes, capsys):
    _, second = two_sandboxes
    (second / "code.py").write_text("print('v2')\n")
    _commit_all(second, "feat: add prompt marker")
    _git("tag", "v9.9.9", cwd=second)

    chooser = fake_input("")
    selected = resolve_sandbox(None, current_project=PROJECT_NAME, home=fake_home, input_fn=chooser)

    assert selected is not None and selected.name == second.name
    out = capsys.readouterr().out
    sha = _git("rev-parse", "--short", "HEAD", cwd=second)
    assert f"  1) {second.name} [clean]  -  {sha} v9.9.9 feat: add prompt marker (2026-11-07)" in out
    # Untagged sandbox: the hash and subject are still shown, without a tag.
    older_line = out.splitlines()[2]
    assert f"  2) uxproj_{FIRST_ID} [clean]  -  " in older_line
    assert "v9.9.9" not in older_line


def test_picker_truncates_long_subject(fake_home, main_repo, two_sandboxes, capsys):
    _, second = two_sandboxes
    subject = "feat: " + "x" * 100
    (second / "code.py").write_text("print('long')\n")
    _commit_all(second, subject)

    resolve_sandbox(None, current_project=PROJECT_NAME, home=fake_home, input_fn=fake_input(""))

    out = capsys.readouterr().out
    assert subject not in out
    assert "..." in out
    assert "feat: xxxx" in out


def test_picker_tolerates_sandbox_without_git(fake_home, main_repo, two_sandboxes, capsys):
    broken = fake_home / "sandboxes" / f"{PROJECT_NAME}_20261108_000000"
    broken.mkdir()

    selected = resolve_sandbox(
        None, current_project=PROJECT_NAME, home=fake_home, input_fn=fake_input("")
    )

    assert selected is not None and selected.name == broken.name
    out = capsys.readouterr().out
    assert f"  1) {broken.name} [no-git]  -  (no commits) (2026-11-08)" in out


# --- partial argument ---------------------------------------------------------------


def test_partial_id_single_match_auto_selects(fake_home, main_repo, two_sandboxes, capsys):
    selected = resolve_sandbox("003629", home=fake_home)

    assert selected is not None and selected.name == f"uxproj_{FIRST_ID}"
    assert "[i] Auto-selected sandbox: uxproj_20261107_003629" in capsys.readouterr().out


def test_exact_full_name_is_silent(fake_home, main_repo, two_sandboxes, capsys):
    selected = resolve_sandbox(f"uxproj_{SECOND_ID}", home=fake_home)

    assert selected is not None and selected.name == f"uxproj_{SECOND_ID}"
    assert "Auto-selected" not in capsys.readouterr().out


def test_partial_id_multiple_matches_lists_them(fake_home, main_repo, two_sandboxes, capsys):
    chooser = fake_input("")

    selected = resolve_sandbox("20261107", home=fake_home, input_fn=chooser)

    assert selected is not None and selected.name == f"uxproj_{SECOND_ID}"
    out = capsys.readouterr().out
    assert "[i] Multiple sandboxes match '20261107':" in out
    assert f"  1) uxproj_{SECOND_ID} [clean]  -  " in out
    assert f"  2) uxproj_{FIRST_ID} [clean]  -  " in out
    assert chooser.calls == ["Select sandbox [1-2] (default 1): "]


def test_partial_id_no_match_errors(fake_home, main_repo, two_sandboxes, capsys):
    assert resolve_sandbox("999999", home=fake_home) is None
    assert "no sandbox matches '999999'" in capsys.readouterr().err


# --- cli wiring -----------------------------------------------------------------------


def test_cmd_merge_auto_selects_and_merges(fake_home, main_repo, monkeypatch, capsys):
    sandbox = create_sandbox(main_repo, PROJECT_NAME)
    monkeypatch.setattr(Path, "cwd", lambda: main_repo)
    captured = {}

    def fake_merge(name):
        captured["name"] = name
        return 0

    monkeypatch.setattr(cli, "merge_sandbox", fake_merge)

    assert cli.cmd_merge(None) == 0
    assert captured["name"] == sandbox.name
    assert f"[i] Auto-selected sandbox: {sandbox.name}" in capsys.readouterr().out


def test_cmd_merge_multiple_uses_piped_stdin_choice(fake_home, main_repo, two_sandboxes, monkeypatch):
    monkeypatch.setattr(Path, "cwd", lambda: main_repo)
    # "2" is now the older sandbox, so the extra confirmation is required.
    monkeypatch.setattr(sys, "stdin", io.StringIO("2\ny\n"))
    captured = {}

    def fake_merge(name):
        captured["name"] = name
        return 0

    monkeypatch.setattr(cli, "merge_sandbox", fake_merge)

    assert cli.cmd_merge(None) == 0
    assert captured["name"] == f"uxproj_{FIRST_ID}"


def test_cmd_clean_auto_selects_and_cleans(fake_home, main_repo, monkeypatch, capsys):
    sandbox = create_sandbox(main_repo, PROJECT_NAME)
    monkeypatch.setattr(Path, "cwd", lambda: main_repo)
    captured = {}

    def fake_clean(name, force=False):
        captured["args"] = (name, force)
        return 0

    monkeypatch.setattr(cli, "clean_sandbox", fake_clean)

    assert cli.cmd_clean(None, force=True) == 0
    assert captured["args"] == (sandbox.name, True)


def test_cli_main_merge_without_argument_routes(fake_home, main_repo, monkeypatch):
    sandbox = create_sandbox(main_repo, PROJECT_NAME)
    monkeypatch.setattr(sys, "argv", ["airlock", "merge"])
    monkeypatch.setattr(Path, "cwd", lambda: main_repo)
    captured = {}

    def fake_merge(name):
        captured["name"] = name
        return 0

    monkeypatch.setattr(cli, "merge_sandbox", fake_merge)

    assert cli.main() == 0
    assert captured["name"] == sandbox.name


def test_resolve_returns_sandbox_info_shape(fake_home, main_repo, two_sandboxes):
    selected = resolve_sandbox("003629", home=fake_home)

    assert isinstance(selected, SandboxInfo)
    assert selected.project == PROJECT_NAME
    assert selected.id == FIRST_ID
    assert selected.branch == f"sandbox/{FIRST_ID}"
