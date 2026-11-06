"""Sandbox creation for `airlock enter`.

Sandbox layout:
- Sandboxes live under <home>/sandboxes/<project>_<id>.
- A bare "push target" remote lives under <home>/.local/share/airlock/remotes/.
- Inside the sandbox: origin = bare remote, upstream = the project's
  original GitHub remote with push disabled via `no_push://<project>`.
"""

from __future__ import annotations

import re
import subprocess
from datetime import datetime
from pathlib import Path

__all__ = [
    "SandboxError",
    "sandbox_root",
    "bare_remote_path",
    "new_sandbox_id",
    "parse_sandbox_name",
    "create_sandbox",
]


class SandboxError(Exception):
    """Raised when a sandbox setup command fails."""


def _run(cmd: list[str], cwd: Path | None = None) -> str:
    """Run a command, raising SandboxError with stderr on failure."""
    try:
        proc = subprocess.run(cmd, cwd=cwd, check=True, capture_output=True, text=True)
    except FileNotFoundError as e:
        raise SandboxError(f"Command not found: {cmd[0]}") from e
    except subprocess.CalledProcessError as e:
        stderr = (e.stderr or "").strip()
        raise SandboxError(f"Command failed: {' '.join(cmd)}\n{stderr}") from e
    return proc.stdout


def sandbox_root(home: Path | None = None) -> Path:
    """Return <home>/sandboxes/, creating it if missing."""
    root = (home or Path.home()) / "sandboxes"
    root.mkdir(parents=True, exist_ok=True)
    return root


def bare_remote_path(project_name: str) -> Path:
    """Return the bare-remote path for a project, creating parent dirs."""
    path = Path.home() / ".local" / "share" / "airlock" / "remotes" / f"{project_name}.git"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def new_sandbox_id() -> str:
    """Return a unique-per-second sandbox id."""
    return datetime.now().strftime("%Y%m%d_%H%M%S")


# <project>_<YYYYMMDD_HHMMSS>, with an optional _<n> uniqueness counter.
_SANDBOX_NAME_RE = re.compile(r"^(?P<project>.+)_(?P<id>\d{8}_\d{6})(?:_(?P<counter>\d+))?$")


def parse_sandbox_name(name: str) -> tuple[str, str]:
    """Split '<project>_<YYYYMMDD_HHMMSS>[_<n>]' into (project_name, sandbox_id).

    The counter suffix is stripped: same-second collisions keep the original
    branch id, mirroring the branch derivation in cli._sandbox_branch.
    """
    match = _SANDBOX_NAME_RE.match(name)
    if match is None:
        raise SandboxError(
            f"invalid sandbox name {name!r}; expected '<project>_<YYYYMMDD_HHMMSS>'"
        )
    return match.group("project"), match.group("id")


def _origin_url(project_root: Path) -> str | None:
    """Return the project's origin URL, or None if it has no origin remote."""
    proc = subprocess.run(
        ["git", "-C", str(project_root), "remote", "get-url", "origin"],
        check=False,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


def create_sandbox(project_root: Path, project_name: str) -> Path:
    """Clone project_root into a fresh sandbox and configure its remotes."""
    sandbox_id = new_sandbox_id()
    root_dir = sandbox_root()

    sandbox_path = root_dir / f"{project_name}_{sandbox_id}"
    counter = 2
    while sandbox_path.exists():
        sandbox_path = root_dir / f"{project_name}_{sandbox_id}_{counter}"
        counter += 1

    bare = bare_remote_path(project_name)
    if not bare.exists():
        _run(["git", "init", "--bare", "-q", str(bare)])

    # `git clone --local` sets origin to the local source path, so remember the
    # original remote URL and point upstream at it after the rename.
    upstream_url = _origin_url(project_root)

    _run(["git", "clone", "--local", str(project_root), str(sandbox_path)])

    _run(["git", "remote", "rename", "origin", "upstream"], cwd=sandbox_path)
    if upstream_url:
        _run(["git", "remote", "set-url", "upstream", upstream_url], cwd=sandbox_path)
    _run(
        ["git", "remote", "set-url", "--push", "upstream", f"no_push://{project_name}"],
        cwd=sandbox_path,
    )
    _run(["git", "remote", "add", "origin", str(bare)], cwd=sandbox_path)
    _run(["git", "checkout", "-b", f"sandbox/{sandbox_id}"], cwd=sandbox_path)

    has_version_config = (
        (project_root / ".tool-versions").exists() or (project_root / "mise.toml").exists()
    )
    if has_version_config:
        _run(["mise", "install"], cwd=sandbox_path)

    return sandbox_path
