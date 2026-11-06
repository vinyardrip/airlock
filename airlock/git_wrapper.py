"""Guarded git wrapper for airlock sandboxes (Task 1.4).

`airlock/bin/git` is prepended to PATH inside sandbox subshells. While the
sandbox is active (AIRLOCK_ACTIVE=1) the wrapper:

- scrubs every GIT_* environment variable before calling the real git
  (prevents e.g. GIT_SSH_COMMAND injection);
- forces `-c core.hooksPath=/dev/null` and `-c diff.external=` so repo-level
  hooks and external diff drivers cannot run (user-supplied -c overrides for
  exactly those two keys are dropped);
- blocks `git push` to anything but origin (the sandbox bare remote under
  ~/.local/share/airlock/remotes/);
- blocks `git checkout`/`git switch` outside ~/sandboxes/ (i.e. in the main
  project directory);
- blocks `git config --global`/`--system` and creating/re-pointing/renaming
  the `upstream` remote;
- logs blocked attempts to ~/sandboxes/.airlock_git_guard.log.

Outside an active sandbox the wrapper is a pure pass-through. Inside one,
everything not blocked is passed to the real git with the original exit code.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

__all__ = [
    "WrapperError",
    "wrapper_bin_dir",
    "find_real_git",
    "scrub_env",
    "sandbox_root",
    "guard_decision",
    "guard_log_path",
    "log_blocked",
    "main",
]

# Injected before every guarded git invocation. User-supplied -c pairs for
# these same keys are dropped, so a later -c cannot re-enable hooks or an
# external diff driver.
FORCED_CONFIG_ARGS = ("-c", "core.hooksPath=/dev/null", "-c", "diff.external=")

_PROTECTED_CONFIG_KEYS = {"core.hookspath", "diff.external"}

# Pre-subcommand git options that consume a separate value argument.
_PRE_SUBCOMMAND_VALUE_OPTS = {
    "-c",
    "-C",
    "--git-dir",
    "--work-tree",
    "--namespace",
    "--super-prefix",
}

# git push options that consume a separate value argument.
_PUSH_VALUE_OPTS = {"-o", "--push-option", "--receive-pack"}

# git remote add options that consume a separate value argument.
_REMOTE_ADD_VALUE_OPTS = {"-t", "-m"}

Query = Callable[[list[str]], "str | None"]


class WrapperError(Exception):
    """Raised when the wrapper cannot locate the real git binary."""


def wrapper_bin_dir() -> Path:
    """Directory containing the `git` wrapper script (prepended to PATH)."""
    return Path(__file__).resolve().parent / "bin"


def sandbox_root(home: Path | None = None) -> Path:
    """The directory all sandboxes live under (mirrors airlock.sandbox)."""
    return (home or Path.home()) / "sandboxes"


def airlock_remotes_dir(home: Path | None = None) -> Path:
    """Parent of the per-project bare remotes (mirrors sandbox.bare_remote_path)."""
    return (home or Path.home()) / ".local" / "share" / "airlock" / "remotes"


def guard_log_path(home: Path | None = None) -> Path:
    """Log file that records every blocked attempt."""
    return sandbox_root(home) / ".airlock_git_guard.log"


def find_real_git(path_var: str | None = None, *, wrapper_dir: Path | None = None) -> str:
    """Locate the real git binary on PATH, skipping the wrapper's own directory."""
    own_dir = (wrapper_dir or wrapper_bin_dir()).resolve()
    own_file = own_dir / "git"
    search = (path_var if path_var is not None else os.environ.get("PATH", "")).split(
        os.pathsep
    )
    for entry in search:
        if not entry.strip():
            continue
        directory = Path(entry).expanduser()
        try:
            if directory.resolve() == own_dir:
                continue
            candidate = directory / "git"
            if not candidate.is_file() or not os.access(candidate, os.X_OK):
                continue
            if candidate.resolve() == own_file:
                continue  # a symlink to ourselves would recurse forever
        except OSError:
            continue
        return str(candidate)
    raise WrapperError("real git not found on PATH")


def scrub_env(env: dict[str, str] | None = None) -> dict[str, str]:
    """Return a copy of env without any GIT_* variables."""
    source = os.environ if env is None else env
    return {k: v for k, v in source.items() if not k.startswith("GIT_")}


def split_subcommand(args: list[str]) -> tuple[str | None, list[str]]:
    """Split git argv into (subcommand, rest); None if no subcommand is given."""
    i = 0
    while i < len(args):
        arg = args[i]
        if arg in _PRE_SUBCOMMAND_VALUE_OPTS:
            i += 2  # option plus its value; overshoot ends the loop
            continue
        if arg.startswith("-"):
            # Plain flags, --opt=value forms and merged -ckey=val forms.
            i += 1
            continue
        return arg, args[i + 1 :]
    return None, []


def _default_query(cwd: Path) -> Query:
    """Read-only git lookups against the real binary (injectable for tests)."""

    def query(args: list[str]) -> str | None:
        try:
            real = find_real_git()
            proc = subprocess.run(
                [real, *args], cwd=cwd, capture_output=True, text=True, check=False
            )
        except (WrapperError, OSError):
            return None
        if proc.returncode != 0:
            return None
        return proc.stdout.strip() or None

    return query


def _first_push_repo(rest: list[str]) -> str | None:
    """The <repository> operand of git push (--repo=... wins over positional)."""
    repo: str | None = None
    i = 0
    while i < len(rest):
        arg = rest[i]
        if arg == "--repo" and i + 1 < len(rest):
            repo = rest[i + 1]
            i += 2
            continue
        if arg.startswith("--repo="):
            repo = arg[len("--repo=") :]
            i += 1
            continue
        if arg in _PUSH_VALUE_OPTS:
            i += 2
            continue
        if arg.startswith("-"):
            i += 1
            continue
        if repo is None:
            repo = arg
        i += 1
    return repo


def _is_airlock_bare(url: str | None, home: Path | None) -> bool:
    """True if url points inside the airlock bare-remote directory."""
    if not url:
        return False
    raw = url[len("file://") :] if url.startswith("file://") else url
    try:
        path = Path(raw).expanduser().resolve()
        return path.is_relative_to(airlock_remotes_dir(home).resolve())
    except (OSError, ValueError):
        return False


def _push_decision(rest: list[str], *, home: Path | None, query: Query) -> tuple[bool, str | None]:
    repo = _first_push_repo(rest)
    if repo is None:
        # No explicit <repository>: git uses branch.<name>.remote, defaulting
        # to origin. Ask the real git what it would pick.
        branch = query(["symbolic-ref", "--quiet", "--short", "HEAD"])
        remote = query(["config", "--get", f"branch.{branch}.remote"]) if branch else None
        if remote and remote != "origin":
            return True, (
                f"push would default to remote '{remote}'; "
                "only 'origin' (the sandbox bare remote) is allowed"
            )
        return False, None
    origin_url = query(["remote", "get-url", "origin"])
    if repo == "origin":
        if origin_url is None or _is_airlock_bare(origin_url, home):
            return False, None
        return True, (
            f"origin points at '{origin_url}', not the sandbox bare remote"
        )
    if origin_url and repo == origin_url:
        return False, None  # the bare remote by URL is still origin
    return True, f"push target '{repo}' is not 'origin' (the sandbox bare remote)"


def _config_decision(rest: list[str]) -> tuple[bool, str | None]:
    for arg in rest:
        if arg in ("--global", "--system"):
            return True, f"git config {arg} is blocked inside sandboxes"
    return False, None


def _remote_decision(rest: list[str]) -> tuple[bool, str | None]:
    if not rest:
        return False, None
    sub, operands = rest[0], rest[1:]
    if sub not in ("add", "set-url", "rename"):
        return False, None
    positionals: list[str] = []
    i = 0
    while i < len(operands):
        arg = operands[i]
        if sub == "add" and arg in _REMOTE_ADD_VALUE_OPTS:
            i += 2
            continue
        if arg.startswith("-"):
            i += 1
            continue
        positionals.append(arg)
        i += 1
    if sub in ("add", "set-url") and positionals and positionals[0] == "upstream":
        return True, f"git remote {sub} upstream is blocked inside sandboxes"
    if sub == "rename" and len(positionals) >= 2 and positionals[1] == "upstream":
        return True, "git remote rename <name> upstream is blocked inside sandboxes"
    return False, None


def guard_decision(
    args: list[str],
    *,
    cwd: Path,
    home: Path | None = None,
    active: bool = True,
    query: Query | None = None,
) -> tuple[bool, str | None]:
    """Decide whether this invocation must be blocked.

    Returns (blocked, reason). `query` runs read-only git lookups (injectable
    for tests); `active` mirrors AIRLOCK_ACTIVE=1 in the sandbox subshell.
    """
    if not active:
        return False, None
    sub, rest = split_subcommand(args)
    if sub is None:
        return False, None
    if query is None:
        query = _default_query(cwd)
    if sub == "push":
        return _push_decision(rest, home=home, query=query)
    if sub in ("checkout", "switch"):
        sandboxes = sandbox_root(home)
        try:
            outside = not cwd.resolve().is_relative_to(sandboxes.resolve())
        except OSError:
            outside = True
        if outside:
            return True, (
                f"'{sub}' is blocked outside {sandboxes}; work on branches in the sandbox"
            )
        return False, None
    if sub == "config":
        return _config_decision(rest)
    if sub == "remote":
        return _remote_decision(rest)
    return False, None


def log_blocked(
    args: list[str],
    cwd: Path,
    reason: str,
    *,
    home: Path | None = None,
    log_path: Path | None = None,
) -> None:
    """Append one line per blocked attempt; never let logging break the block."""
    path = log_path or guard_log_path(home)
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    line = f"{stamp} cwd={cwd} git {shlex.join(args)} :: {reason}\n"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line)
    except OSError:
        pass


def _drop_protected_config(args: list[str]) -> list[str]:
    """Remove user -c pairs that would re-enable hooks or external diffs."""
    out: list[str] = []
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "-c" and i + 1 < len(args):
            key = args[i + 1].split("=", 1)[0].strip().lower()
            if key not in _PROTECTED_CONFIG_KEYS:
                out.append(arg)
                out.append(args[i + 1])
            i += 2
            continue
        if arg.startswith("-c") and len(arg) > 2:  # merged -ckey=value form
            key = arg[2:].split("=", 1)[0].strip().lower()
            if key not in _PROTECTED_CONFIG_KEYS:
                out.append(arg)
            i += 1
            continue
        out.append(arg)
        i += 1
    return out


def build_real_argv(real_git: str, args: list[str]) -> list[str]:
    """Full argv for the real git: forced config first, then user args."""
    return [real_git, *FORCED_CONFIG_ARGS, *_drop_protected_config(args)]


def _run_git(argv: list[str], env: dict[str, str], *, guarded: bool) -> int:
    try:
        real = find_real_git()
    except WrapperError as exc:
        print(f"[airlock] {exc}", file=sys.stderr)
        return 127
    cmd = build_real_argv(real, argv) if guarded and argv else [real, *argv]
    run_env = scrub_env(env) if guarded else env
    try:
        proc = subprocess.run(cmd, env=run_env, check=False)
    except OSError as exc:
        print(f"[airlock] cannot run {real}: {exc}", file=sys.stderr)
        return 127
    return proc.returncode


def main(
    argv: list[str] | None = None,
    *,
    env: dict[str, str] | None = None,
    home: Path | None = None,
    query: Query | None = None,
    log_path: Path | None = None,
) -> int:
    """Entry point for airlock/bin/git. Returns the process exit code."""
    argv = list(sys.argv[1:]) if argv is None else list(argv)
    environ = os.environ if env is None else env
    active = environ.get("AIRLOCK_ACTIVE") == "1"

    if active:
        cwd = Path.cwd()
        blocked, reason = guard_decision(argv, cwd=cwd, home=home, active=True, query=query)
        if blocked:
            target = log_path or guard_log_path(home)
            log_blocked(argv, cwd, reason, log_path=target)
            print(f"[airlock] blocked: git {shlex.join(argv)}", file=sys.stderr)
            print(f"[airlock] reason: {reason}", file=sys.stderr)
            print(f"[airlock] logged to: {target}", file=sys.stderr)
            return 1

    return _run_git(argv, environ, guarded=active)
