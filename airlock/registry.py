"""Sandbox listing, selection and removal (Task 1.6 + smart selection).

`airlock list` — every sandbox under <home>/sandboxes/ as an aligned table
(Project, ID, Created, Branch, Status), newest first, where Status is [clean]
or [dirty].

`airlock clean <project>_<id>` — deletes the sandbox directory, refusing
while the tree is dirty or the sandbox branch has commits not pushed to the
bare remote. --force bypasses those checks but still requires typing 'yes';
even a fully clean sandbox is never deleted without that confirmation.

`resolve_sandbox` — shared smart selection for merge/clean: an omitted or
partial argument is resolved against the sandboxes of the current project,
auto-selecting when unambiguous and offering a numbered list otherwise.
Sandboxes are always ordered newest first, so the default (1) is the latest
work rather than the oldest archive.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import NamedTuple

from airlock.sandbox import SandboxError, parse_sandbox_name, sandbox_root

__all__ = [
    "CleanError",
    "SandboxInfo",
    "list_sandboxes",
    "format_sandbox_table",
    "resolve_sandbox",
    "clean_sandbox",
]


class CleanError(Exception):
    """Raised when `airlock clean` must refuse."""


_ID_FORMAT = "%Y%m%d_%H%M%S"


class SandboxInfo(NamedTuple):
    name: str  # full directory name: <project>_<id>[_<n>]
    project: str
    id: str
    branch: str
    status: str  # "[clean]" / "[dirty]" / "[no-git]"
    created: str = ""  # human-readable "YYYY-MM-DD HH:MM" from the id


def _created_label(sandbox_id: str) -> str:
    """Turn a sandbox id (YYYYMMDD_HHMMSS) into a readable timestamp."""
    try:
        return datetime.strptime(sandbox_id, _ID_FORMAT).strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return "unknown"


def _sort_key(info: SandboxInfo) -> tuple[str, float, str]:
    """Project ascending, then newest sandbox first (negated timestamp)."""
    try:
        _project, sandbox_id = parse_sandbox_name(info.name)
        timestamp = datetime.strptime(sandbox_id, _ID_FORMAT).timestamp()
    except (SandboxError, ValueError):
        timestamp = 0.0
    return (info.project, -timestamp, info.name)


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
        infos.append(
            SandboxInfo(
                entry.name, project, display_id, branch, status, _created_label(_sandbox_id)
            )
        )
    infos.sort(key=_sort_key)
    return infos, skipped


def format_sandbox_table(infos: list[SandboxInfo]) -> str:
    """Aligned table with headers Project, ID, Created, Branch, Status."""
    headers = ("Project", "ID", "Created", "Branch", "Status")
    rows = [
        (info.project, info.id, info.created, info.branch, info.status) for info in infos
    ]
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


def resolve_sandbox(
    arg: str | None,
    *,
    current_project: str | None = None,
    home: Path | None = None,
    input_fn: Callable[[str], str] | None = None,
) -> SandboxInfo | None:
    """Resolve an omitted or partial sandbox argument to one sandbox.

    - arg is None: restrict to sandboxes of `current_project`; a single one
      is auto-selected, several are offered as a numbered list. The list is
      newest first, so Enter -> 1 picks the latest sandbox.
    - arg given: substring match against sandbox names; one exact match is
      returned silently, a single partial match is auto-selected with a
      notice, several matches are offered as a numbered list.

    Selecting anything but the newest entry requires an extra [y/N] confirm.
    Ctrl+C / EOF abort with "[i] Aborted.". Returns None (after printing an
    [err]/[i] line) when nothing matches or the user aborted.
    """
    infos, _skipped = list_sandboxes(home)
    if arg is None:
        if current_project is None:
            print(
                "[err] cannot detect the current project; pass a sandbox name",
                file=sys.stderr,
            )
            return None
        candidates = [info for info in infos if info.project == current_project]
        if not candidates:
            print(
                f"[err] no sandboxes found for project '{current_project}'",
                file=sys.stderr,
            )
            return None
        if len(candidates) == 1:
            print(f"[i] Auto-selected sandbox: {candidates[0].name}")
            return candidates[0]
        return _select_interactive(
            candidates, f"Multiple sandboxes for '{current_project}'", input_fn
        )

    candidates = [info for info in infos if arg in info.name]
    if not candidates:
        print(f"[err] no sandbox matches '{arg}'", file=sys.stderr)
        return None
    if len(candidates) == 1:
        if candidates[0].name != arg:
            print(f"[i] Auto-selected sandbox: {candidates[0].name}")
        return candidates[0]
    return _select_interactive(candidates, f"Multiple sandboxes match '{arg}'", input_fn)


_OLDER_WARNING = (
    "[!] Warning: You selected an older sandbox. "
    "Newer sandboxes exist. Are you sure? [y/N]: "
)


def _yellow(text: str) -> str:
    """Colour the warning yellow when stdout is a terminal."""
    if sys.stdout.isatty():
        return f"\033[33m{text}\033[0m"
    return text


def _select_interactive(
    candidates: list[SandboxInfo],
    header: str,
    input_fn: Callable[[str], str] | None,
) -> SandboxInfo | None:
    """Numbered picker (newest first); Enter picks 1, invalid input re-prompts."""
    print(f"[i] {header}:")
    for number, info in enumerate(candidates, 1):
        latest = "  [latest]" if number == 1 else ""
        print(
            f"  {number}) {info.name}{latest}  {info.created}  "
            f"{info.branch}  {info.status}"
        )
    if input_fn is None:
        input_fn = input
    while True:
        try:
            raw = input_fn(f"Select sandbox [1-{len(candidates)}] (default 1): ")
        except (EOFError, KeyboardInterrupt):
            print("\n[i] Aborted.")
            return None
        raw = raw.strip()
        if not raw:
            return candidates[0]
        if raw.isdigit() and 1 <= int(raw) <= len(candidates):
            selected = candidates[int(raw) - 1]
            if selected is candidates[0]:
                return selected
            # Anything below the newest entry can clobber fresher work.
            try:
                answer = input_fn(_yellow(_OLDER_WARNING))
            except (EOFError, KeyboardInterrupt):
                print("\n[i] Aborted.")
                return None
            if answer.strip().lower() in ("y", "yes"):
                return selected
            print("[i] Aborted; older sandbox not selected.")
            return None
        print(f"[!] invalid selection: {raw!r}", file=sys.stderr)
