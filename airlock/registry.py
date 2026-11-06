"""Sandbox listing and removal (Task 1.6).

`airlock list` — every sandbox under <home>/sandboxes/ as an aligned table
(Project, ID, Branch, Status) where Status is [clean] or [dirty].

`airlock clean <project>_<id>` — deletes the sandbox directory, refusing
while the tree is dirty or the sandbox branch has commits not pushed to the
bare remote. --force bypasses those checks but still requires typing 'yes';
even a fully clean sandbox is never deleted without that confirmation.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import NamedTuple

from airlock.sandbox import SandboxError, parse_sandbox_name, sandbox_root

__all__ = [
    "CleanError",
    "SandboxInfo",
    "list_sandboxes",
    "format_sandbox_table",
    "clean_sandbox",
]


class CleanError(Exception):
    """Raised when `airlock clean` must refuse."""


class SandboxInfo(NamedTuple):
    name: str  # full directory name: <project>_<id>[_<n>]
    project: str
    id: str
    branch: str
    status: str  # "[clean]" / "[dirty]" / "[no-git]"


def _run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=False)


def _git_out(args: list[str], cwd: Path) -> str | None:
    """stdout of a read-only git command, or None if it failed."""
    proc = _run(["git", *args], cwd)
    return proc.stdout.strip() if proc.returncode == 0 else None


def _probe_sandbox(sandbox: Path) -> tuple[str, str]:
    """(branch, status) of one sandbox, tolerating broken repositories."""
    current = _git_out(["branch", "--show-current"], sandbox)
    if current is None:
        branch, status = "(unknown)", "[no-git]"
    else:
        branch = current or "(detached)"
        porcelain = _git_out(["status", "--porcelain"], sandbox)
        if porcelain is None:
            status = "[no-git]"
        else:
            status = "[dirty]" if porcelain else "[clean]"
    return branch, status


def list_sandboxes(home: Path | None = None) -> tuple[list[SandboxInfo], int]:
    """All sandboxes under the sandboxes root, plus the skipped-entry count."""
    infos: list[SandboxInfo] = []
    skipped = 0
    for entry in sorted(sandbox_root(home).iterdir()):
        try:
            project, _sandbox_id = parse_sandbox_name(entry.name)
        except SandboxError:
            skipped += 1
            continue
        if not entry.is_dir():
            skipped += 1
            continue
        branch, status = _probe_sandbox(entry)
        # Display the id as it appears in the directory name (counter
        # included), so same-second sandboxes remain distinguishable.
        display_id = entry.name[len(project) + 1 :]
        infos.append(SandboxInfo(entry.name, project, display_id, branch, status))
    infos.sort(key=lambda info: (info.project, info.id, info.name))
    return infos, skipped


def format_sandbox_table(infos: list[SandboxInfo]) -> str:
    """Aligned table with headers Project, ID, Branch, Status."""
    headers = ("Project", "ID", "Branch", "Status")
    rows = [(info.project, info.id, info.branch, info.status) for info in infos]
    widths = [
        max([len(header), *[len(row[col]) for row in rows]])
        for col, header in enumerate(headers)
    ]
    lines = ["  ".join(h.ljust(w) for h, w in zip(headers, widths)).rstrip()]
    lines.append("  ".join("-" * w for w in widths))
    lines.extend(
        "  ".join(cell.ljust(w) for cell, w in zip(row, widths)).rstrip() for row in rows
    )
    return "\n".join(lines)


def _default_clean_confirm(sandbox: Path) -> bool:
    print(f"Delete {sandbox} permanently? Type 'yes' to confirm: ", end="", flush=True)
    return sys.stdin.readline().strip().lower() == "yes"


def _unpushed_detail(sandbox: Path, branch: str) -> str | None:
    """Why the branch is not in sync with the bare remote, or None if it is."""
    local = _git_out(
        ["rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"], sandbox
    )
    remote_out = _git_out(["ls-remote", "origin", f"refs/heads/{branch}"], sandbox)
    if remote_out is None:
        return "cannot query the bare remote ('origin')"
    remote = remote_out.split()[0] if remote_out else ""
    if local == remote:
        return None
    count: str | None = None
    if local:
        # Never pushed: count every commit on the branch.
        spec = f"{remote}..{local}" if remote else local
        count = _git_out(["rev-list", "--count", spec], sandbox)
    if count and count.isdigit() and int(count) > 0:
        return f"{count} unpushed commit(s)"
    return "branch diverged from the bare remote"


def clean_sandbox(
    name: str,
    *,
    force: bool = False,
    confirm: Callable[[Path], bool] | None = None,
    home: Path | None = None,
) -> int:
    """Delete sandbox `<project>_<id>`. Returns exit code (0 on success).

    Refuses on a dirty tree or commits missing from the bare remote unless
    `force` is set; the confirmation prompt always applies.
    """
    try:
        _project_name, sandbox_id = parse_sandbox_name(name)
    except SandboxError as exc:
        raise CleanError(str(exc)) from exc

    sandbox = sandbox_root(home) / name
    if not sandbox.is_dir():
        raise CleanError(f"sandbox not found: {sandbox}")
    branch = f"sandbox/{sandbox_id}"

    if not force:
        porcelain = _git_out(["status", "--porcelain"], sandbox)
        if porcelain is None:
            raise CleanError(
                f"sandbox {name} is not a working git repository; use --force to delete"
            )
        if porcelain:
            raise CleanError(
                f"sandbox {name} has uncommitted changes:\n{porcelain}\n"
                "commit them, or use --force (and confirm) to delete anyway"
            )
        detail = _unpushed_detail(sandbox, branch)
        if detail is not None:
            raise CleanError(
                f"sandbox {name} is not fully pushed ({detail}); "
                "push or merge it first, or use --force (and confirm)"
            )

    if confirm is None:
        confirm = _default_clean_confirm
    if not confirm(sandbox):
        print("[i] Aborted; sandbox kept.")
        return 1

    shutil.rmtree(sandbox)
    print(f"[i] Removed sandbox {sandbox}")
    return 0
