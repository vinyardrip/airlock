"""Merge a sandbox branch back into the main project (Task 1.5).

Flow for `airlock merge <project>_<id>` (run from the main project directory):

1. refuse if the sandbox or the main project has uncommitted changes;
2. push the sandbox branch to the project's bare remote;
3. fetch that branch into the main project (FETCH_HEAD);
4. show what will be merged: a --stat summary plus a separate diff restricted
   to version config files (.tool-versions, mise.toml, pnpm-lock.yaml,
   package.json, pyproject.toml);
5. prompt [y/N] — a bare Enter aborts;
6. `git merge FETCH_HEAD`;
7. if .tool-versions or mise.toml changed, run `mise install` in the main
   project (output captured and printed; a missing or failing mise never
   fails the merge itself).
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from collections.abc import Callable
from pathlib import Path

from airlock.project import detect_project_root
from airlock.sandbox import (
    SandboxError,
    bare_remote_path,
    parse_sandbox_name,
    sandbox_root,
)

__all__ = ["MergeError", "VERSION_CONFIG_FILES", "RUNTIME_CONFIG_FILES", "merge_sandbox"]

VERSION_CONFIG_FILES = (
    ".tool-versions",
    "mise.toml",
    "pnpm-lock.yaml",
    "package.json",
    "pyproject.toml",
)

# The subset of version configs that triggers a `mise install` after merging.
RUNTIME_CONFIG_FILES = (".tool-versions", "mise.toml")

Confirm = Callable[[], bool]


class MergeError(Exception):
    """Raised when the merge flow must abort."""


def _run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=False)


def _git_out(args: list[str], cwd: Path) -> str:
    """Run git read-only, returning stdout; raise MergeError on failure."""
    proc = _run(["git", *args], cwd)
    if proc.returncode != 0:
        raise MergeError(f"git {' '.join(args)} failed:\n{(proc.stderr or '').strip()}")
    return proc.stdout


def _default_confirm() -> bool:
    """Interactive [y/N] prompt; a bare Enter (or EOF) means no."""
    print("Proceed with merge? [y/N]: ", end="", flush=True)
    answer = sys.stdin.readline().strip().lower()
    return answer in ("y", "yes")


def _run_mise_install(root: Path) -> None:
    """Run `mise install` in root; never fail the merge over it."""
    try:
        proc = subprocess.run(
            ["mise", "install"], cwd=root, capture_output=True, text=True, check=False
        )
    except FileNotFoundError:
        print("[!] 'mise' not found on PATH; skipping 'mise install'.", file=sys.stderr)
        return
    if proc.stdout.strip():
        print(proc.stdout.strip())
    if proc.returncode != 0:
        print(f"[!] 'mise install' failed (exit {proc.returncode}); merge is unaffected.", file=sys.stderr)
    if proc.stderr.strip():
        print(proc.stderr.strip(), file=sys.stderr)


def merge_sandbox(
    name: str,
    *,
    project_root: Path | None = None,
    home: Path | None = None,
    confirm: Confirm | None = None,
) -> int:
    """Merge sandbox `<project>_<id>` into its main project. Returns exit code.

    `project_root` defaults to detect_project_root() (the caller's cwd);
    `confirm` defaults to an interactive [y/N] prompt (injectable for tests).
    """
    try:
        project_name, sandbox_id = parse_sandbox_name(name)
    except SandboxError as exc:
        raise MergeError(str(exc)) from exc

    sandbox = sandbox_root(home) / name
    if not sandbox.is_dir():
        raise MergeError(f"sandbox not found: {sandbox}")

    root = project_root or detect_project_root()
    if root is None:
        raise MergeError(
            "cannot detect the main project; run 'airlock merge' from the project directory"
        )
    if root.name != project_name:
        raise MergeError(
            f"sandbox {name} belongs to project '{project_name}', but the current "
            f"project is '{root.name}'; cd into the main project directory first"
        )

    status = _run(["git", "status", "--porcelain"], cwd=sandbox).stdout.strip()
    if status:
        raise MergeError(f"sandbox {name} has uncommitted changes:\n{status}")

    main_status = _run(["git", "status", "--porcelain"], cwd=root).stdout.strip()
    if main_status:
        raise MergeError(
            f"main project '{root.name}' has uncommitted changes; "
            f"commit or stash them first:\n{main_status}"
        )

    branch = f"sandbox/{sandbox_id}"
    bare = bare_remote_path(project_name)

    print(f"[i] Pushing {branch} to the bare remote...")
    push = _run(["git", "push", "origin", branch], cwd=sandbox)
    if push.returncode != 0:
        raise MergeError(
            f"pushing {branch} to the bare remote failed:\n{(push.stderr or '').strip()}"
        )

    print(f"[i] Fetching {branch} into '{root.name}'...")
    fetch = _run(["git", "fetch", str(bare), branch], cwd=root)
    if fetch.returncode != 0:
        raise MergeError(
            f"fetching {branch} from the bare remote failed:\n{(fetch.stderr or '').strip()}"
        )

    ahead = _git_out(["rev-list", "--count", "HEAD..FETCH_HEAD"], root).strip()
    if ahead == "0":
        print("[i] Nothing to merge: the sandbox branch has no new commits.")
        return 0
    print(f"[i] {ahead} commit(s) will be merged.")

    stat = _git_out(["diff", "--stat", "HEAD...FETCH_HEAD"], root).rstrip("\n")
    if stat:
        print("[i] Files changed:")
        print(textwrap.indent(stat, "  "))

    config_diff = _git_out(
        ["diff", "HEAD...FETCH_HEAD", "--", *VERSION_CONFIG_FILES], root
    ).rstrip("\n")
    if config_diff:
        print(f"--- version config diff ({', '.join(VERSION_CONFIG_FILES)}) ---")
        print(config_diff)
        print("--- end version config diff ---")
    else:
        print("[i] No version config changes.")

    # Computed before the merge: afterwards FETCH_HEAD is HEAD's ancestor and
    # the diff would always come back empty.
    runtime_changed = _git_out(
        ["diff", "--name-only", "HEAD...FETCH_HEAD", "--", *RUNTIME_CONFIG_FILES], root
    ).split()

    if confirm is None:
        confirm = _default_confirm
    if not confirm():
        print("[i] Aborted; nothing merged.")
        return 1

    merge = _run(["git", "merge", "FETCH_HEAD"], cwd=root)
    if merge.returncode != 0:
        detail = "\n".join(
            part.strip() for part in (merge.stdout, merge.stderr) if part and part.strip()
        )
        raise MergeError(
            f"git merge failed (resolve conflicts in '{root.name}' manually):\n{detail}"
        )
    if merge.stdout.strip():
        print(merge.stdout.strip())

    if runtime_changed:
        print(f"[i] Version config changed ({', '.join(runtime_changed)}); running 'mise install'...")
        _run_mise_install(root)

    print(f"[i] Merged {branch} into '{root.name}'.")
    return 0
