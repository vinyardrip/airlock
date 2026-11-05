"""Smoke test for airlock.cli.cmd_enter (real mode).

Subshell entry is tested manually in Task 1.3 verification.
"""

import subprocess
from pathlib import Path

import airlock.cli as cli
from airlock.cli import cmd_enter


def test_cmd_enter_creates_sandbox_and_prints_project(tmp_path, monkeypatch, capsys):
    project = tmp_path / "fakeproj"
    project.mkdir()
    (project / ".git").mkdir()
    (project / ".tool-versions").write_text("python 3.15.2\n")

    monkeypatch.setattr(Path, "cwd", lambda: project)

    fake_sandbox = tmp_path / "fakeproj_20260101_000000"
    fake_sandbox.mkdir()
    calls = []

    def fake_create_sandbox(root, name):
        calls.append((root, name))
        return fake_sandbox

    monkeypatch.setattr(cli, "create_sandbox", fake_create_sandbox)

    def fake_run(*args, **kwargs):
        calls.append(("subshell", kwargs.get("cwd"), kwargs.get("env")))
        return subprocess.CompletedProcess(args[0] if args else [], 0)

    monkeypatch.setattr(cli.subprocess, "run", fake_run)

    rc = cmd_enter(None)

    out = capsys.readouterr().out
    assert rc == 0
    assert f"[i] Project: {project.name}" in out
    assert f"[i] Sandbox: {fake_sandbox}" in out
    assert "[i] Entering subshell" in out
    assert "[i] Exited sandbox." in out

    assert len(calls) == 2
    assert calls[0] == (project.resolve(), project.name)
    subshell_cwd, subshell_env = calls[1][1], calls[1][2]
    assert subshell_cwd == fake_sandbox
    assert subshell_env["AIRLOCK_ACTIVE"] == "1"
    assert subshell_env["AIRLOCK_MAIN"] == str(project.resolve())
    assert subshell_env["PS1"] == f"(airlock:{project.name}) \\w $ "
