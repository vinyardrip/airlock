"""Sandbox shell setup: environment, ZDOTDIR shim and subshell launch.

The marker is driven exclusively by AIRLOCK_* environment variables. We never
inspect cwd to decide whether we are in a sandbox, and we never source the
user's ~/.zshrc: a different prompt is the signal.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

__all__ = [
    "SHIM_TEMPLATE",
    "zsh_shim_dir",
    "prepare_zsh_shim",
    "cleanup_zsh_shim",
    "base_info",
    "is_zsh",
    "sandbox_zdotdir",
]

SHIM_TEMPLATE = Path(__file__).resolve().parent / "prompts" / "sandbox.zshrc"


def zsh_shim_dir(sandbox_path: Path, home: Path | None = None) -> Path:
    """Return the shim directory for one sandbox.

    The full sandbox directory name is used (not just the timestamp id) so
    same-second sandboxes with a _2 counter cannot share a ZDOTDIR.
    """
    cache_root = Path(
        os.environ.get("XDG_CACHE_HOME") or ((home or Path.home()) / ".cache")
    )
    return cache_root / "airlock" / "shell" / sandbox_path.name


def prepare_zsh_shim(sandbox_path: Path, home: Path | None = None) -> Path:
    """Materialize the shipped .zshrc template into a private ZDOTDIR."""
    shim_dir = zsh_shim_dir(sandbox_path, home)
    shim_dir.mkdir(parents=True, exist_ok=True)
    (shim_dir / ".zshrc").write_text(
        SHIM_TEMPLATE.read_text(encoding="utf-8"), encoding="utf-8"
    )
    return shim_dir


def cleanup_zsh_shim(sandbox_path: Path, home: Path | None = None) -> None:
    """Remove the generated ZDOTDIR after the subshell exits."""
    shutil.rmtree(zsh_shim_dir(sandbox_path, home), ignore_errors=True)


def _git_out(repo: Path, *args: str) -> str:
    """Read-only git lookup.

    Deliberately uses Popen rather than run/check_output: both of those are
    stubbed by the subshell tests, while these probes must stay real.
    """
    try:
        proc = subprocess.Popen(
            ["git", "-C", str(repo), *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        out, _ = proc.communicate()
    except OSError:
        return ""
    return (out or "").strip()


def base_info(project_root: Path) -> tuple[str, str]:
    """Return (short sha, branch) of the main copy at clone time."""
    sha = _git_out(project_root, "rev-parse", "--short", "HEAD")
    ref = _git_out(project_root, "symbolic-ref", "--quiet", "--short", "HEAD")
    if not ref:
        ref = _git_out(project_root, "rev-parse", "--abbrev-ref", "HEAD") or "HEAD"
    return sha, ref


def is_zsh(shell: str) -> bool:
    """True for zsh paths such as /bin/zsh or /usr/bin/zsh-5.9."""
    return "zsh" in Path(shell).name


@contextmanager
def sandbox_zdotdir(
    sandbox_path: Path,
    env: dict[str, str],
    *,
    shell: str | None = None,
    home: Path | None = None,
) -> Iterator[None]:
    """Temporarily point ZDOTDIR at the generated shim for zsh subshells.

    On exit the caller's env dict is restored to its original ZDOTDIR value
    (or has the key removed) and the shim directory is deleted. For non-zsh
    shells nothing is created and ZDOTDIR is left untouched.
    """
    shell = shell or env.get("SHELL", "/bin/bash")
    original = env.get("ZDOTDIR")
    shim_dir: Path | None = None

    if is_zsh(shell):
        shim_dir = prepare_zsh_shim(sandbox_path, home)
        env["ZDOTDIR"] = str(shim_dir)

    try:
        yield
    finally:
        if shim_dir is not None:
            if original is None:
                env.pop("ZDOTDIR", None)
            else:
                env["ZDOTDIR"] = original
            cleanup_zsh_shim(sandbox_path, home)
