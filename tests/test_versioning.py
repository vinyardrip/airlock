"""Tests for airlock.versioning (Tasks 1.7-1.10).

'mise latest' is mocked by patching subprocess.run in the literal unit tests
and by a stub `mise` binary on PATH in the flow tests; `mise install` likewise
never reaches the real tool. The test `pytest` is a stub too, so running the
suite never starts an actual test runner.
"""

import io
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

import airlock.cli as cli
import airlock.versioning as versioning_mod
from airlock.versioning import (
    VersioningError,
    check_versions,
    migrate_versions,
    parse_mise_tools,
    parse_tool_versions,
    parse_tool_versions_strict,
    read_versions,
    render_mise_toml,
    update_tool,
)

LATEST = {"python": "3.15.2", "node": "20.11.0"}


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    return home


@pytest.fixture
def stub_tools(tmp_path, monkeypatch):
    """Stub mise (latest/install) and pytest on PATH, plus a MISE_LOG hook."""
    stub = tmp_path / "stubbin"
    stub.mkdir()
    (stub / "mise").write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "latest" ]; then\n'
        '    echo "${FAKE_LATEST-9.9.9}"\n'
        "    exit 0\n"
        "fi\n"
        'if [ "$1" = "install" ]; then\n'
        '    echo "mise-install-stub-ok"\n'
        '    [ -n "$MISE_LOG" ] && echo ran >> "$MISE_LOG"\n'
        '    exit "${FAKE_MISE_EXIT:-0}"\n'
        "fi\n"
        'echo "mise-stub: unhandled $*" >&2\n'
        "exit 1\n"
    )
    (stub / "mise").chmod(0o755)
    (stub / "pytest").write_text(
        '#!/bin/sh\necho "pytest-stub-ok"\nexit "${FAKE_TEST_EXIT:-0}"\n'
    )
    (stub / "pytest").chmod(0o755)
    monkeypatch.setenv("PATH", str(stub) + os.pathsep + os.environ.get("PATH", ""))
    return stub


def mock_latest(query=None):
    """Injected latest_query backed by the LATEST table (no subprocess at all)."""
    return lambda tool: LATEST.get(tool)


# --- parsing (Task 1.7) -----------------------------------------------------------


def test_parse_tool_versions_lenient():
    text = "# comment\n\npython 3.12.0\nnode 20 18\n"
    assert parse_tool_versions(text) == {"python": "3.12.0", "node": "20"}


def test_parse_mise_tools_shapes():
    text = textwrap.dedent(
        """
        [tools]
        python = "3.12"
        node = 20
        ruby = ["3.1", "3.0"]
        go = { version = "1.21" }
        [env]
        python = "not-a-version"
        """
    )
    assert parse_mise_tools(text) == {"python": "3.12", "node": "20", "ruby": "3.1", "go": "1.21"}


def test_parse_mise_tools_malformed_raises():
    with pytest.raises(VersioningError, match="cannot parse"):
        parse_mise_tools("[tools\nbroken")


def test_read_versions_prefers_mise_toml_and_warns(tmp_path, capsys):
    """Requirement: mise.toml > .tool-versions; warn about the ignored file."""
    (tmp_path / ".tool-versions").write_text("python 3.11.0\n")
    (tmp_path / "mise.toml").write_text('[tools]\npython = "3.15"\n')

    versions = read_versions(tmp_path)

    assert versions == {"python": "3.15"}
    err = capsys.readouterr().err
    assert "ignored" in err and ".tool-versions" in err


def test_read_versions_tool_versions_only(tmp_path, capsys):
    (tmp_path / ".tool-versions").write_text("python 3.12.0\nnode 20\n")

    assert read_versions(tmp_path) == {"python": "3.12.0", "node": "20"}
    assert capsys.readouterr().err == ""


def test_read_versions_nearest_file_wins(tmp_path):
    (tmp_path / ".tool-versions").write_text("python 3.11.0\n")
    sub = tmp_path / "services" / "api"
    sub.mkdir(parents=True)
    (sub / "mise.toml").write_text('[tools]\npython = "3.13"\nnode = "20"\n')

    assert read_versions(tmp_path, start=sub) == {"python": "3.13", "node": "20"}
    assert read_versions(tmp_path) == {"python": "3.11.0"}


# --- check (Task 1.8) ---------------------------------------------------------------


def test_check_ok_when_within_policy(tmp_path, capsys):
    (tmp_path / "mise.toml").write_text('[tools]\npython = "3.15.0"\n')

    rc = check_versions(tmp_path, latest_query=mock_latest())

    assert rc == 0
    assert "[ok]" in capsys.readouterr().out
    assert "OUTDATED" not in capsys.readouterr().out


def test_check_flags_outdated_when_more_than_one_release_line_behind(tmp_path, capsys):
    (tmp_path / "mise.toml").write_text(
        '[tools]\npython = "3.12.0"\nnode = "18.0.0"\n'
    )  # latest: python 3.15.2, node 20.11.0

    rc = check_versions(tmp_path, latest_query=mock_latest())

    assert rc == 1
    out = capsys.readouterr().out
    assert "[OUTDATED] (3 minor versions behind)" in out  # python 3.12 -> 3.15
    assert "[OUTDATED] (2 major versions behind)" in out  # node 18 -> 20


def test_check_one_release_line_behind_is_within_policy(tmp_path, capsys):
    (tmp_path / ".tool-versions").write_text("python 3.14.0\n")  # latest 3.15.2

    rc = check_versions(tmp_path, latest_query=mock_latest())

    assert rc == 0
    assert "[behind]" in capsys.readouterr().out


def test_check_policy_file_overrides_threshold(fake_home, tmp_path, capsys):
    policy_dir = fake_home / ".config" / "airlock"
    policy_dir.mkdir(parents=True)
    (policy_dir / "policy.toml").write_text("outdated_majors = 3\n")
    (tmp_path / ".tool-versions").write_text("python 3.12.0\n")  # gap 3

    rc = check_versions(tmp_path, home=fake_home, latest_query=mock_latest())

    assert rc == 0  # gap 3 is not > threshold 3
    assert "[behind]" in capsys.readouterr().out  # flagged, but within policy


def test_check_reports_missing_coverage(tmp_path, capsys):
    (tmp_path / ".tool-versions").write_text("python 3.12.0\n")
    (tmp_path / "package.json").write_text('{"engines": {"node": ">=20", "pnpm": ">=9"}}')
    (tmp_path / "Cargo.toml").write_text("[package]\nname = 'x'\n")

    rc = check_versions(tmp_path, latest_query=mock_latest())

    assert rc == 1
    out = capsys.readouterr().out
    assert "[MISSING]" in out
    assert "node" in out and "package.json engines" in out
    assert "rust" in out and "Cargo.toml" in out


def test_check_requires_python_implies_python(tmp_path, capsys):
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\nrequires-python = ">=3.11"\n')

    rc = check_versions(tmp_path, latest_query=mock_latest())

    assert rc == 1
    out = capsys.readouterr().out
    assert "python" in out and "requires-python" in out


def test_check_suggests_migrate_when_only_tool_versions(tmp_path, capsys):
    (tmp_path / ".tool-versions").write_text("python 3.15.0\n")

    check_versions(tmp_path, latest_query=mock_latest())

    assert "airlock migrate versions" in capsys.readouterr().out


def test_check_handles_uncomparable_versions(tmp_path, capsys):
    (tmp_path / ".tool-versions").write_text("python ref:main\n")

    rc = check_versions(tmp_path, latest_query=mock_latest())

    assert rc == 0
    assert "[unknown]" in capsys.readouterr().out


def test_mise_latest_via_mocked_subprocess(monkeypatch):
    """Requirement: 'mise latest' goes through subprocess.run, mocked here."""
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="4.2.7\n", stderr="")

    monkeypatch.setattr(versioning_mod.subprocess, "run", fake_run)

    assert versioning_mod._mise_latest("node") == "4.2.7"
    assert calls == [["mise", "latest", "node"]]


def test_mise_latest_missing_binary_returns_none(monkeypatch):
    def fake_run(cmd, **kwargs):
        raise FileNotFoundError("mise")

    monkeypatch.setattr(versioning_mod.subprocess, "run", fake_run)

    assert versioning_mod._mise_latest("node") is None


def test_mise_latest_failure_returncode(monkeypatch):
    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="unknown tool")

    monkeypatch.setattr(versioning_mod.subprocess, "run", fake_run)

    assert versioning_mod._mise_latest("node") is None


# --- update (Task 1.9) ---------------------------------------------------------------


@pytest.fixture
def fake_sandbox(fake_home):
    sandbox = fake_home / "sandboxes" / "demoproj_20260101_000000"
    sandbox.mkdir(parents=True)
    return sandbox


def test_update_tool_versions_file_and_run_tests(
    fake_sandbox, stub_tools, monkeypatch, tmp_path, capsys
):
    (fake_sandbox / ".tool-versions").write_text("# comment\npython 3.12.0\nnode 18.0.0\n")
    (fake_sandbox / "pyproject.toml").write_text("[tool.pytest.ini_options]\ntestpaths = ['tests']\n")
    mise_log = tmp_path / "mise.log"
    monkeypatch.setenv("FAKE_LATEST", "3.15.2")
    monkeypatch.setenv("MISE_LOG", str(mise_log))

    rc = update_tool("python", project_root=fake_sandbox)

    assert rc == 0
    assert (fake_sandbox / ".tool-versions").read_text() == (
        "# comment\npython 3.15.2\nnode 18.0.0\n"
    )
    assert mise_log.exists()  # mise install really went through the stub
    out = capsys.readouterr().out
    assert "python: 3.12.0 -> 3.15.2" in out
    assert "mise-install-stub-ok" in out
    assert "pytest-stub-ok" in out
    assert "Tests passed" in out


def test_update_mise_toml_preserves_other_sections(fake_sandbox, stub_tools, monkeypatch, capsys):
    (fake_sandbox / "mise.toml").write_text(
        "# top comment\n"
        "[tools]\n"
        'python = "3.12"  # pin comment\n'
        'node = "20"\n'
        "\n[env]\n"
        'python = "not-a-version"\n'
    )
    monkeypatch.setenv("FAKE_LATEST", "3.15.2")

    rc = update_tool("python", project_root=fake_sandbox)

    assert rc == 0
    assert (fake_sandbox / "mise.toml").read_text() == (
        "# top comment\n"
        "[tools]\n"
        'python = "3.15.2"  # pin comment\n'
        'node = "20"\n'
        "\n[env]\n"
        'python = "not-a-version"\n'
    )
    assert "python: 3.12 -> 3.15.2" in capsys.readouterr().out


def test_update_refuses_outside_sandbox(tmp_path):
    with pytest.raises(VersioningError, match="not inside a sandbox"):
        update_tool("python", project_root=tmp_path)


def test_update_refuses_unpinned_tool(fake_sandbox, stub_tools):
    (fake_sandbox / ".tool-versions").write_text("python 3.12.0\n")

    with pytest.raises(VersioningError, match="not pinned"):
        update_tool("golang", project_root=fake_sandbox)


def test_update_refuses_when_latest_unknown(fake_sandbox, stub_tools, monkeypatch):
    (fake_sandbox / ".tool-versions").write_text("python 3.12.0\n")
    monkeypatch.setenv("FAKE_LATEST", "")  # stub echoes an empty line

    with pytest.raises(VersioningError, match="cannot determine the latest version"):
        update_tool("python", project_root=fake_sandbox)


def test_update_reports_failing_tests(fake_sandbox, stub_tools, monkeypatch, capsys):
    (fake_sandbox / ".tool-versions").write_text("python 3.12.0\n")
    (fake_sandbox / "pyproject.toml").write_text("[tool.pytest.ini_options]\n")
    monkeypatch.setenv("FAKE_LATEST", "3.15.2")
    monkeypatch.setenv("FAKE_TEST_EXIT", "3")

    rc = update_tool("python", project_root=fake_sandbox)

    assert rc == 3
    assert "Tests failed (exit 3)" in capsys.readouterr().out


def test_update_without_test_command(fake_sandbox, stub_tools, monkeypatch, capsys):
    (fake_sandbox / ".tool-versions").write_text("python 3.12.0\n")
    monkeypatch.setenv("FAKE_LATEST", "3.15.2")

    rc = update_tool("python", project_root=fake_sandbox)

    assert rc == 0
    assert "No test command detected" in capsys.readouterr().out


# --- migrate (Task 1.10) ---------------------------------------------------------------


def test_migrate_strict_parser_accepts_simple_lines():
    entries = parse_tool_versions_strict("# c\npython 3.12.0\nnode 20\n")
    assert entries == [("python", "3.12.0"), ("node", "20")]


def test_migrate_strict_parser_refuses_complex_syntax():
    text = (
        "python path:/opt/py\n"
        "nodejs ref:main\n"
        "ruby 3.1.0 3.2.0\n"
        "vanilla\n"
        "node 18\n"
        "node 20\n"
    )
    with pytest.raises(VersioningError) as excinfo:
        parse_tool_versions_strict(text)
    message = str(excinfo.value)
    assert "line 1" in message and "path:" in message
    assert "line 2" in message and "ref:" in message
    assert "line 3" in message and "fallback list" in message
    assert "line 4" in message and "missing version" in message
    assert "line 6" in message and "duplicate entry for 'node'" in message


def test_render_mise_toml_quotes_odd_keys():
    content = render_mise_toml([("python", "3.12.0"), ("my.tool", "1.0")])
    assert content == (
        "# Converted from .tool-versions by 'airlock migrate versions'.\n"
        '[tools]\npython = "3.12.0"\n"my.tool" = "1.0"\n'
    )


def test_migrate_writes_mise_toml_and_keeps_backup(tmp_path, capsys):
    (tmp_path / ".tool-versions").write_text("python 3.12.0\nnode 20\n")

    rc = migrate_versions(tmp_path, force=True)

    assert rc == 0
    assert (tmp_path / "mise.toml").read_text() == (
        "# Converted from .tool-versions by 'airlock migrate versions'.\n"
        '[tools]\npython = "3.12.0"\nnode = "20"\n'
    )
    assert (tmp_path / ".tool-versions").exists()  # kept as backup
    assert "backup" in capsys.readouterr().out


def test_migrate_dry_run_prints_diff_without_writing(tmp_path, capsys):
    (tmp_path / ".tool-versions").write_text("python 3.12.0\n")

    rc = migrate_versions(tmp_path, dry_run=True)

    assert rc == 0
    out = capsys.readouterr().out
    assert "dry run" in out
    assert "+python = \"3.12.0\"" in out
    assert not (tmp_path / "mise.toml").exists()


def test_migrate_refuses_existing_mise_toml(tmp_path):
    (tmp_path / ".tool-versions").write_text("python 3.12.0\n")
    (tmp_path / "mise.toml").write_text("[tools]\n")

    with pytest.raises(VersioningError, match="already exists"):
        migrate_versions(tmp_path, force=True)


def test_migrate_refuses_complex_syntax(tmp_path):
    (tmp_path / ".tool-versions").write_text("python path:/opt/py\n")

    with pytest.raises(VersioningError, match="path:"):
        migrate_versions(tmp_path, force=True)
    assert not (tmp_path / "mise.toml").exists()


def test_migrate_refuses_non_interactive_without_force(tmp_path, monkeypatch):
    """pytest's stdin is not a tty: no --force must refuse."""
    (tmp_path / ".tool-versions").write_text("python 3.12.0\n")
    monkeypatch.setattr(sys, "stdin", io.StringIO("y\n"))  # piped, not a tty

    with pytest.raises(VersioningError, match="non-interactive"):
        migrate_versions(tmp_path)
    assert not (tmp_path / "mise.toml").exists()


def test_migrate_confirm_declined_aborts(tmp_path):
    (tmp_path / ".tool-versions").write_text("python 3.12.0\n")

    rc = migrate_versions(tmp_path, confirm=lambda root: False)

    assert rc == 1
    assert not (tmp_path / "mise.toml").exists()


def test_migrate_no_tool_versions(tmp_path):
    with pytest.raises(VersioningError, match="nothing to migrate"):
        migrate_versions(tmp_path, force=True)


# --- cli wiring ---------------------------------------------------------------------------


def test_cmd_update_routes(monkeypatch):
    calls = {}

    def fake_update(tool):
        calls["tool"] = tool
        return 5

    monkeypatch.setattr(cli, "update_tool", fake_update)

    assert cli.cmd_update("python") == 5
    assert calls["tool"] == "python"


def test_cmd_migrate_routes(monkeypatch):
    calls = {}

    def fake_migrate(**kw):
        calls["kw"] = kw
        return 0

    monkeypatch.setattr(cli, "migrate_versions", fake_migrate)

    assert cli.cmd_migrate(dry_run=True, force=False) == 0
    assert calls["kw"] == {"dry_run": True, "force": False}
