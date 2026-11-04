"""Project root detection and container heuristics."""

from __future__ import annotations

import os
from pathlib import Path

PROJECT_MARKER = ".git"

SCAN_EXCLUDE = {
    ".git",
    "node_modules",
    "venv",
    ".venv",
    "__pycache__",
    ".sandbox",
    "dist",
    "build",
    ".pytest_cache",
    ".mypy_cache",
}

SIZE_THRESHOLD_MB = 500
NESTED_PROJECTS_THRESHOLD = 3


def detect_project_root(start: Path | None = None) -> Path | None:
    current = (start or Path.cwd()).resolve()

    for parent in [current, *current.parents]:
        if (parent / PROJECT_MARKER).exists():
            return parent

    return None


def _has_nonempty_gitignore(root: Path) -> bool:
    gitignore = root / ".gitignore"
    if not gitignore.is_file():
        return False

    try:
        for line in gitignore.read_text(encoding="utf-8", errors="ignore").splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                return True
    except OSError:
        return False

    return False


def _find_nested_projects(root: Path, max_depth: int = 2) -> list[Path]:
    found: list[Path] = []
    root_resolved = root.resolve()

    def walk(directory: Path, depth: int) -> None:
        if depth > max_depth:
            return
        try:
            entries = list(directory.iterdir())
        except OSError:
            return
        for entry in entries:
            if not entry.is_dir() or entry.name in SCAN_EXCLUDE:
                continue
            if (entry / PROJECT_MARKER).exists():
                found.append(entry)
                continue
            walk(entry, depth + 1)

    walk(root_resolved, depth=1)
    return found


def _directory_size_mb(root: Path) -> float:
    total = 0
    root_resolved = root.resolve()

    for dirpath, dirnames, filenames in os.walk(root_resolved):
        dirnames[:] = [d for d in dirnames if d not in SCAN_EXCLUDE]
        for f in filenames:
            try:
                total += (Path(dirpath) / f).stat().st_size
            except OSError:
                pass

    return total / (1024 * 1024)


def looks_like_container(root: Path) -> tuple[bool, dict]:
    has_git = (root / PROJECT_MARKER).exists()
    has_gi = _has_nonempty_gitignore(root)
    nested = _find_nested_projects(root)
    size_mb = _directory_size_mb(root)

    reasons: list[str] = []
    if has_git and not has_gi:
        if nested:
            reasons.append(f"{len(nested)} nested .git director{'y' if len(nested) == 1 else 'ies'}")
        if size_mb > SIZE_THRESHOLD_MB:
            reasons.append(f"size {size_mb:.0f} MB > {SIZE_THRESHOLD_MB} MB")
        if len(nested) >= NESTED_PROJECTS_THRESHOLD:
            reasons.append(f"{len(nested)} project-like subdirectories")

    is_container = bool(reasons)

    return is_container, {
        "has_gitignore": has_gi,
        "nested_projects": [p.name for p in nested],
        "size_mb": size_mb,
        "reasons": reasons,
    }
