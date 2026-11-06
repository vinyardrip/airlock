"""Tests for the airlock git wrapper (Task 1.4).

A stub "real git" is placed first on PATH. It answers the wrapper's read-only
queries, then prints a marker plus its argv and any surviving GIT_* env vars,
and honours STUB_EXIT — so tests can definitively assert both that the stub
ran (marker present) and that it was skipped (marker absent) / exit codes.
"""

import os
import subprocess
import textwrap
from pathlib import Path

import pytest

import airlock
import airlock.cli as cli
from airlock import git_wrapper

WRAPPER = Path(airlock.__file__).resolve().parent / "bin" / "git"
STUB_MARKER = "AIRLOCK_STUB_GIT_CALLED"

STUB_SCRIPT = textwrap.dedent(
    """\
    #!/bin/sh
    # Test double for the real git binary (see tests/test_git_wrapper.py).
    if [ "$1" = "remote" ] && [ "$2" = "get-url" ]; then
        echo "${FAKE_ORIGIN_URL:-$HOME/.local/share/airlock/remotes/fakeproj.git}"
        exit 0
    fi
    if [ "$1" = "symbolic-ref" ]; then
        echo "${FAKE_BRANCH:-sandbox/20260101_000000}"
        exit 0
    fi
    if [ "$1" = "config" ]; then
        echo "${FAKE_BRANCH_REMOTE:-origin}"
        exit 0
    fi
    echo "AIRLOCK_STUB_GIT_CALLED"
    for a in "$@"; do
        echo "arg:$a"
    done
    env | grep '^GIT_' | sort
    exit "${STUB_EXIT:-0}"
    """
)


@pytest.fixture
def sandbox_tree(tmp_path):
    """Fake HOME with ~/sandboxes/<proj_1> and an airlock bare remote."""
    home = tmp_path / "home"
    sandbox = home / "sandboxes" / "fakeproj_20260101_000000"
    sandbox.mkdir(parents=True)
    bare = home / ".local" / "share" / "airlock" / "remotes" / "fakeproj.git"
    bare.mkdir(parents=True)
    return home, sandbox


@pytest.fixture
def stub_git(tmp_path):
    stub_dir = tmp_path / "stubbin"
    stub_dir.mkdir()
    stub = stub_dir / "git"
    stub.write_text(STUB_SCRIPT)
    stub.chmod(0o755)
    return stub_dir


def run_wrapper(args, cwd, sandbox_tree, stub_git, extra_env=None):
    home, _ = sandbox_tree
    env = {
        "PATH": f"{stub_git}{os.pathsep}{os.environ['PATH']}",
        "HOME": str(home),
        "AIRLOCK_ACTIVE": "1",
    }
    env.update(extra_env or {})
    return subprocess.run(
        [str(WRAPPER), *args], cwd=cwd, env=env, capture_output=True, text=True
    )


def stub_args(proc):
    return [line[len("arg:") :] for line in proc.stdout.splitlines() if line.startswith("arg:")]


# --- script properties -------------------------------------------------------


def test_wrapper_script_is_executable():
    """Requirement: ships with the executable bit set (tracked as 100755)."""
    assert WRAPPER.is_file()
    assert os.access(WRAPPER, os.X_OK)


# --- pass-through behaviour --------------------------------------------------


def test_passthrough_calls_stub_with_injected_guard_config(tmp_path, sandbox_tree, stub_git):
    proc = run_wrapper(["status"], cwd=tmp_path, sandbox_tree=sandbox_tree, stub_git=stub_git)

    assert proc.returncode == 0
    assert STUB_MARKER in proc.stdout  # definitive: the stub ran
    assert stub_args(proc)[:4] == ["-c", "core.hooksPath=/dev/null", "-c", "diff.external="]
    assert stub_args(proc)[4:] == ["status"]


def test_git_env_vars_are_scrubbed(tmp_path, sandbox_tree, stub_git):
    proc = run_wrapper(
        ["status"],
        cwd=tmp_path,
        sandbox_tree=sandbox_tree,
        stub_git=stub_git,
        extra_env={"GIT_SSH_COMMAND": "evil-ssh", "GIT_PROXY_COMMAND": "evil-proxy"},
    )

    assert proc.returncode == 0
    assert STUB_MARKER in proc.stdout
    # The stub prints surviving GIT_* vars verbatim; none may appear.
    assert not [line for line in proc.stdout.splitlines() if line.startswith("GIT_")]
    assert "evil" not in proc.stdout


def test_stub_exit_code_propagates(tmp_path, sandbox_tree, stub_git):
    proc = run_wrapper(
        ["log"],
        cwd=tmp_path,
        sandbox_tree=sandbox_tree,
        stub_git=stub_git,
        extra_env={"STUB_EXIT": "42"},
    )

    assert proc.returncode == 42
    assert STUB_MARKER in proc.stdout


def test_user_config_c_pairs_pass_through(tmp_path, sandbox_tree, stub_git):
    proc = run_wrapper(
        ["-c", "user.name=t", "commit"], cwd=tmp_path, sandbox_tree=sandbox_tree, stub_git=stub_git
    )

    assert proc.returncode == 0
    assert stub_args(proc) == [
        "-c",
        "core.hooksPath=/dev/null",
        "-c",
        "diff.external=",
        "-c",
        "user.name=t",
        "commit",
    ]


def test_user_cannot_reenable_hooks_or_diff_driver(tmp_path, sandbox_tree, stub_git):
    proc = run_wrapper(
        ["-c", "core.hooksPath=.hooks", "-cdiff.external=pager", "status"],
        cwd=tmp_path,
        sandbox_tree=sandbox_tree,
        stub_git=stub_git,
    )

    assert proc.returncode == 0
    assert stub_args(proc) == ["-c", "core.hooksPath=/dev/null", "-c", "diff.external=", "status"]


def test_no_args_runs_real_git_gracefully(tmp_path, sandbox_tree, stub_git):
    """No argv: delegate unmodified; the real git prints usage (no crash)."""
    proc = run_wrapper([], cwd=tmp_path, sandbox_tree=sandbox_tree, stub_git=stub_git)

    assert proc.returncode == 0  # stub default exit; real git would exit 1 with usage
    assert STUB_MARKER in proc.stdout
    assert stub_args(proc) == []  # no forced -c injection without a subcommand


def test_wrapper_skips_own_dir_on_path(tmp_path, sandbox_tree, stub_git):
    """Wrapper dir first on PATH: must skip itself and find the stub."""
    home, sandbox = sandbox_tree
    env = {
        "PATH": f"{git_wrapper.wrapper_bin_dir()}{os.pathsep}{stub_git}{os.pathsep}{os.environ['PATH']}",
        "HOME": str(home),
        "AIRLOCK_ACTIVE": "1",
    }
    proc = subprocess.run(
        [str(WRAPPER), "status"], cwd=sandbox, env=env, capture_output=True, text=True
    )

    assert proc.returncode == 0
    assert STUB_MARKER in proc.stdout  # reached the stub, not itself


def test_wrapper_is_pure_passthrough_when_not_active(tmp_path, sandbox_tree, stub_git):
    proc = run_wrapper(
        ["push", "upstream"],
        cwd=tmp_path,
        sandbox_tree=sandbox_tree,
        stub_git=stub_git,
        extra_env={"AIRLOCK_ACTIVE": "0"},
    )

    assert proc.returncode == 0
    assert STUB_MARKER in proc.stdout
    assert stub_args(proc) == ["push", "upstream"]  # unmodified: no guard, no injection


# --- push guard ---------------------------------------------------------------


def test_push_upstream_blocked(tmp_path, sandbox_tree, stub_git):
    home, sandbox = sandbox_tree
    proc = run_wrapper(["push", "upstream", "HEAD"], cwd=sandbox, sandbox_tree=sandbox_tree, stub_git=stub_git)

    assert proc.returncode != 0
    assert STUB_MARKER not in proc.stdout  # definitive: real git never ran
    assert "blocked" in proc.stderr

    log = home / "sandboxes" / ".airlock_git_guard.log"
    assert log.is_file()
    content = log.read_text()
    assert "git push upstream HEAD" in content
    assert str(sandbox) in content


def test_push_origin_allowed(tmp_path, sandbox_tree, stub_git):
    _, sandbox = sandbox_tree
    proc = run_wrapper(["push", "origin", "HEAD"], cwd=sandbox, sandbox_tree=sandbox_tree, stub_git=stub_git)

    assert proc.returncode == 0
    assert STUB_MARKER in proc.stdout
    assert stub_args(proc)[4:] == ["push", "origin", "HEAD"]


def test_push_origin_blocked_when_origin_is_not_airlock_bare(tmp_path, sandbox_tree, stub_git):
    _, sandbox = sandbox_tree
    proc = run_wrapper(
        ["push", "origin"],
        cwd=sandbox,
        sandbox_tree=sandbox_tree,
        stub_git=stub_git,
        extra_env={"FAKE_ORIGIN_URL": "https://github.com/evil/repo.git"},
    )

    assert proc.returncode != 0
    assert STUB_MARKER not in proc.stdout
    assert "blocked" in proc.stderr


def test_push_to_bare_remote_url_allowed(tmp_path, sandbox_tree, stub_git):
    home, sandbox = sandbox_tree
    bare_url = str(home / ".local" / "share" / "airlock" / "remotes" / "fakeproj.git")
    proc = run_wrapper(["push", bare_url, "HEAD"], cwd=sandbox, sandbox_tree=sandbox_tree, stub_git=stub_git)

    assert proc.returncode == 0
    assert STUB_MARKER in proc.stdout


def test_push_to_url_blocked(tmp_path, sandbox_tree, stub_git):
    _, sandbox = sandbox_tree
    proc = run_wrapper(
        ["push", "https://github.com/evil/repo.git", "main"],
        cwd=sandbox,
        sandbox_tree=sandbox_tree,
        stub_git=stub_git,
    )

    assert proc.returncode != 0
    assert STUB_MARKER not in proc.stdout
    assert "blocked" in proc.stderr


def test_bare_push_blocked_when_branch_remote_is_upstream(tmp_path, sandbox_tree, stub_git):
    _, sandbox = sandbox_tree
    proc = run_wrapper(
        ["push"],
        cwd=sandbox,
        sandbox_tree=sandbox_tree,
        stub_git=stub_git,
        extra_env={"FAKE_BRANCH_REMOTE": "upstream"},
    )

    assert proc.returncode != 0
    assert STUB_MARKER not in proc.stdout
    assert "blocked" in proc.stderr


def test_bare_push_allowed_when_branch_remote_is_origin(tmp_path, sandbox_tree, stub_git):
    _, sandbox = sandbox_tree
    proc = run_wrapper(["push"], cwd=sandbox, sandbox_tree=sandbox_tree, stub_git=stub_git)

    assert proc.returncode == 0
    assert STUB_MARKER in proc.stdout


# --- checkout / switch guard ---------------------------------------------------


def test_checkout_blocked_outside_sandboxes(tmp_path, sandbox_tree, stub_git):
    home, _ = sandbox_tree
    main_dir = tmp_path / "mainproj"
    main_dir.mkdir()
    proc = run_wrapper(["checkout", "main"], cwd=main_dir, sandbox_tree=sandbox_tree, stub_git=stub_git)

    assert proc.returncode != 0
    assert STUB_MARKER not in proc.stdout
    assert "blocked" in proc.stderr
    assert str(home / "sandboxes") in proc.stderr


def test_switch_blocked_outside_sandboxes(tmp_path, sandbox_tree, stub_git):
    main_dir = tmp_path / "mainproj"
    main_dir.mkdir()
    proc = run_wrapper(["switch", "main"], cwd=main_dir, sandbox_tree=sandbox_tree, stub_git=stub_git)

    assert proc.returncode != 0
    assert STUB_MARKER not in proc.stdout
    assert "blocked" in proc.stderr


def test_checkout_allowed_inside_sandbox(tmp_path, sandbox_tree, stub_git):
    _, sandbox = sandbox_tree
    proc = run_wrapper(["checkout", "-b", "feature"], cwd=sandbox, sandbox_tree=sandbox_tree, stub_git=stub_git)

    assert proc.returncode == 0
    assert STUB_MARKER in proc.stdout


# --- config / remote guards ----------------------------------------------------


@pytest.mark.parametrize("flag", ["--global", "--system"])
def test_config_global_and_system_blocked(tmp_path, sandbox_tree, stub_git, flag):
    _, sandbox = sandbox_tree
    proc = run_wrapper(
        ["config", flag, "user.name", "t"], cwd=sandbox, sandbox_tree=sandbox_tree, stub_git=stub_git
    )

    assert proc.returncode != 0
    assert STUB_MARKER not in proc.stdout
    assert "blocked" in proc.stderr


def test_local_config_allowed(tmp_path, sandbox_tree, stub_git):
    _, sandbox = sandbox_tree
    proc = run_wrapper(
        ["config", "user.name", "t"], cwd=sandbox, sandbox_tree=sandbox_tree, stub_git=stub_git
    )

    assert proc.returncode == 0
    assert "blocked" not in proc.stderr


@pytest.mark.parametrize(
    "args",
    [
        ["remote", "add", "upstream", "https://github.com/example/x.git"],
        ["remote", "add", "-t", "main", "upstream", "https://github.com/example/x.git"],
        ["remote", "set-url", "upstream", "https://github.com/example/x.git"],
        ["remote", "set-url", "--push", "upstream", "https://github.com/example/x.git"],
        ["remote", "rename", "origin", "upstream"],
    ],
)
def test_upstream_remote_manipulation_blocked(tmp_path, sandbox_tree, stub_git, args):
    _, sandbox = sandbox_tree
    proc = run_wrapper(args, cwd=sandbox, sandbox_tree=sandbox_tree, stub_git=stub_git)

    assert proc.returncode != 0
    assert STUB_MARKER not in proc.stdout
    assert "blocked" in proc.stderr


def test_remote_add_origin_allowed(tmp_path, sandbox_tree, stub_git):
    _, sandbox = sandbox_tree
    proc = run_wrapper(
        ["remote", "add", "origin", "/tmp/somewhere.git"],
        cwd=sandbox,
        sandbox_tree=sandbox_tree,
        stub_git=stub_git,
    )

    assert proc.returncode == 0
    assert STUB_MARKER in proc.stdout


def test_push_upstream_blocked_behind_global_flags(tmp_path, sandbox_tree, stub_git):
    """`git -C <sandbox> push upstream` must be blocked too."""
    _, sandbox = sandbox_tree
    proc = run_wrapper(
        ["-C", str(sandbox), "push", "upstream"],
        cwd=tmp_path,
        sandbox_tree=sandbox_tree,
        stub_git=stub_git,
    )

    assert proc.returncode != 0
    assert STUB_MARKER not in proc.stdout
    assert "blocked" in proc.stderr


# --- unit tests for helpers ----------------------------------------------------


def test_scrub_env_unit():
    env = {"GIT_DIR": "x", "GIT_SSH_COMMAND": "y", "PATH": "keep", "EDITOR": "vim"}

    scrubbed = git_wrapper.scrub_env(env)

    assert scrubbed == {"PATH": "keep", "EDITOR": "vim"}
    assert "GIT_DIR" in env  # original untouched


def test_drop_protected_config_unit():
    out = git_wrapper._drop_protected_config(
        ["-c", "user.name=t", "-c", "core.hooksPath=.hooks", "-cdiff.external=x", "commit"]
    )

    assert out == ["-c", "user.name=t", "commit"]


def test_first_push_repo_unit():
    parse = git_wrapper._first_push_repo
    assert parse(["origin", "main"]) == "origin"
    assert parse(["--atomic", "upstream"]) == "upstream"
    assert parse(["--repo=upstream"]) == "upstream"
    assert parse(["--repo", "upstream", "main"]) == "upstream"
    assert parse(["-o", "key=value", "origin"]) == "origin"
    assert parse([]) is None


def test_find_real_git_skips_wrapper_dir(tmp_path, stub_git):
    path_var = f"{git_wrapper.wrapper_bin_dir()}{os.pathsep}{stub_git}{os.pathsep}{os.environ['PATH']}"

    found = git_wrapper.find_real_git(path_var)

    assert Path(found) == stub_git / "git"


def test_find_real_git_missing_raises(tmp_path):
    with pytest.raises(git_wrapper.WrapperError):
        git_wrapper.find_real_git(str(tmp_path / "nowhere"))


def test_guard_decision_inactive_allows_everything(tmp_path):
    blocked, _ = git_wrapper.guard_decision(
        ["push", "upstream"], cwd=tmp_path, home=tmp_path, active=False, query=lambda a: "upstream"
    )

    assert blocked is False


# --- cli wiring ----------------------------------------------------------------


def test_cmd_enter_prepends_wrapper_dir_to_subshell_path(tmp_path, monkeypatch):
    project = tmp_path / "fakeproj"
    project.mkdir()
    (project / ".git").mkdir()
    fake_sandbox = tmp_path / "fakeproj_20260101_000000"
    fake_sandbox.mkdir()
    monkeypatch.setattr(Path, "cwd", lambda: project)
    monkeypatch.setattr(cli, "create_sandbox", lambda root, name: fake_sandbox)
    captured = {}

    def fake_run(*args, **kwargs):
        captured["cwd"] = kwargs.get("cwd")
        captured["env"] = kwargs.get("env")
        return subprocess.CompletedProcess([], 0)

    monkeypatch.setattr(cli.subprocess, "run", fake_run)

    assert cli.cmd_enter(None) == 0

    env = captured["env"]
    assert env["PATH"].startswith(f"{git_wrapper.wrapper_bin_dir()}{os.pathsep}")
    assert env["AIRLOCK_ACTIVE"] == "1"
