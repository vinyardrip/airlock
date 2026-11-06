"""Version config adapter: read, check, update, migrate (Tasks 1.7-1.10).

Formats (read support):
- ``.tool-versions`` — simple ``tool version`` lines (asdf-style); comments
  and blank lines ignored; a fallback list keeps its first version.
- ``mise.toml`` — ``[tools]`` table via tomllib; string / number values,
  fallback lists (first entry wins) and ``{version = "..."}`` tables are
  understood; ``path:``/``ref:``-style tables are not resolvable here.

Effective resolution follows mise: from the starting directory up to the
project root, the nearest file wins per tool; within one directory
``mise.toml`` beats ``.tool-versions`` (both present -> mise.toml is parsed
and a warning about the ignored .tool-versions goes to stderr).
"""

from __future__ import annotations

import difflib
import json
import re
import subprocess
import sys
import tomllib
from collections.abc import Callable
from pathlib import Path
from typing import NamedTuple

from airlock.project import detect_project_root
from airlock.sandbox import sandbox_root

__all__ = [
    "VersioningError",
    "VersionSource",
    "read_versions",
    "resolve_versions",
    "check_versions",
    "update_tool",
    "migrate_versions",
]

TOOL_VERSIONS_FILE = ".tool-versions"
MISE_FILE = "mise.toml"

# Default policy: flag a tool when its pinned major is more than this many
# majors behind 'mise latest'. Overridable in ~/.config/airlock/policy.toml.
DEFAULT_OUTDATED_MAJORS = 1

LatestQuery = Callable[[str], "str | None"]
Confirm = Callable[[Path], bool]


class VersioningError(Exception):
    """Raised when a versioning command must abort."""


class VersionSource(NamedTuple):
    tool: str
    version: str
    path: Path  # the file that defines this tool (nearest wins)
    fmt: str  # "tool-versions" | "mise"


# --- parsing -----------------------------------------------------------------


def parse_tool_versions(text: str) -> dict[str, str]:
    """Lenient .tool-versions parser: first version per tool wins."""
    versions: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        tokens = line.split()
        versions[tokens[0]] = tokens[1]
    return versions


def _coerce_version(value: object) -> str | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (str, int, float)):
        return str(value)
    return None


def parse_mise_tools(text: str) -> dict[str, str]:
    """[tools] table of a mise.toml; fallback lists collapse to first entry."""
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise VersioningError(f"cannot parse {MISE_FILE}: {exc}") from exc
    tools = data.get("tools", {})
    if not isinstance(tools, dict):
        raise VersioningError(f"{MISE_FILE} [tools] must be a table")
    versions: dict[str, str] = {}
    for tool, value in tools.items():
        if isinstance(value, dict):
            value = value.get("version")  # {path=...} stays unresolvable
        if isinstance(value, list):
            value = value[0] if value else None
        version = _coerce_version(value) if value is not None else None
        if version is not None:
            versions[tool] = version
    return versions


def _dir_config(directory: Path, warn: bool) -> dict[str, VersionSource]:
    """Version sources defined in one directory (mise.toml > .tool-versions)."""
    mise = directory / MISE_FILE
    tool_versions = directory / TOOL_VERSIONS_FILE
    if mise.is_file():
        if tool_versions.is_file() and warn:
            print(
                f"[!] {TOOL_VERSIONS_FILE} is ignored: both {TOOL_VERSIONS_FILE} "
                f"and {MISE_FILE} exist in {directory}; mise applies {MISE_FILE}",
                file=sys.stderr,
            )
        try:
            text = mise.read_text(encoding="utf-8")
        except OSError as exc:
            raise VersioningError(f"cannot read {mise}: {exc}") from exc
        return {
            tool: VersionSource(tool, version, mise, "mise")
            for tool, version in parse_mise_tools(text).items()
        }
    if tool_versions.is_file():
        try:
            text = tool_versions.read_text(encoding="utf-8")
        except OSError as exc:
            raise VersioningError(f"cannot read {tool_versions}: {exc}") from exc
        return {
            tool: VersionSource(tool, version, tool_versions, "tool-versions")
            for tool, version in parse_tool_versions(text).items()
        }
    return {}


def resolve_versions(
    project_root: Path, *, start: Path | None = None, warn: bool = True
) -> dict[str, VersionSource]:
    """Effective sources per mise rules: nearest directory wins per tool."""
    root = project_root.resolve()
    current = (start or project_root).resolve()
    if not current.is_relative_to(root):
        current = root
    chain: list[Path] = []
    while True:
        chain.append(current)
        if current == root or current.parent == current:
            break
        current = current.parent
    resolved: dict[str, VersionSource] = {}
    for directory in chain:  # nearest first
        for tool, source in _dir_config(directory, warn).items():
            resolved.setdefault(tool, source)
    return resolved


def read_versions(
    project_root: Path, *, start: Path | None = None, warn: bool = True
) -> dict[str, str]:
    """Effective tool versions for the project (mise.toml > .tool-versions)."""
    return {
        tool: source.version
        for tool, source in resolve_versions(project_root, start=start, warn=warn).items()
    }


# --- check (Task 1.8) ----------------------------------------------------------

# A pinned version's "release line": the first two numeric components as one
# index (python 3.12 -> 30012, node 20.11 -> 20011, ripgrep 14 -> 14000).
# Comparing release lines (not just semver majors) is what makes the policy
# meaningful for python-style 3.x versioning: 3.12 vs 3.15 is 3 lines behind.
_RELEASE_LINE = re.compile(r"v?(?P<major>\d+)(?:\.(?P<minor>\d+))?")


def _release_info(version: str) -> tuple[int, int, int] | None:
    """(release-line index, major, minor) or None when not numeric."""
    match = _RELEASE_LINE.match(version.strip())
    if match is None:
        return None
    major = int(match.group("major"))
    minor = int(match.group("minor") or 0)
    return major * 10_000 + minor, major, minor


def _mise_latest(tool: str) -> str | None:
    try:
        proc = subprocess.run(
            ["mise", "latest", tool], capture_output=True, text=True, check=False
        )
    except OSError:
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


def _outdated_threshold(home: Path | None) -> int:
    path = (home or Path.home()) / ".config" / "airlock" / "policy.toml"
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return DEFAULT_OUTDATED_MAJORS
    except (OSError, tomllib.TOMLDecodeError) as exc:
        print(f"[!] unreadable policy file {path} ({exc}); using default", file=sys.stderr)
        return DEFAULT_OUTDATED_MAJORS
    value = data.get("outdated_majors", DEFAULT_OUTDATED_MAJORS)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        print(
            f"[!] policy 'outdated_majors' must be a non-negative integer; using default",
            file=sys.stderr,
        )
        return DEFAULT_OUTDATED_MAJORS
    return value


def _used_tools(project_root: Path) -> dict[str, str]:
    """Tools the project uses, inferred from manifests; value = where found."""
    used: dict[str, str] = {}
    pyproject = project_root / "pyproject.toml"
    if pyproject.is_file():
        try:
            data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError) as exc:
            print(f"[!] cannot read {pyproject}: {exc}", file=sys.stderr)
        else:
            requires = data.get("project", {}).get("requires-python")
            if requires is not None:
                used["python"] = "pyproject.toml requires-python"
    package_json = project_root / "package.json"
    if package_json.is_file():
        try:
            data = json.loads(package_json.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            print(f"[!] cannot read {package_json}: {exc}", file=sys.stderr)
        else:
            engines = data.get("engines", {})
            if isinstance(engines, dict):
                for tool in engines:
                    used.setdefault(str(tool), "package.json engines")
    if (project_root / "Cargo.toml").is_file():
        used.setdefault("rust", "Cargo.toml")
    return used


def check_versions(
    project_root: Path, *, home: Path | None = None, latest_query: LatestQuery | None = None
) -> int:
    """Print currency and coverage findings. Exit 1 when problems are found."""
    if latest_query is None:
        latest_query = _mise_latest
    root = project_root
    sources = resolve_versions(root)
    threshold = _outdated_threshold(home)

    print(f"[i] Version check for '{root.name}':")
    if not sources:
        print(f"[!] no version config ({MISE_FILE} / {TOOL_VERSIONS_FILE}) found in {root}")
    findings = 0
    for tool, source in sorted(sources.items()):
        latest = latest_query(tool)
        if latest is None:
            status = "[unknown] ('mise latest' unavailable)"
        else:
            pinned_info, latest_info = _release_info(source.version), _release_info(latest)
            if pinned_info is None or latest_info is None:
                status = f"[unknown] (cannot compare {source.version!r} with {latest!r})"
            else:
                gap = latest_info[0] - pinned_info[0]
                if gap < 0:
                    status = "[ok] (pinned ahead of 'mise latest')"
                elif gap > threshold:
                    if latest_info[1] == pinned_info[1]:
                        detail = f"{latest_info[2] - pinned_info[2]} minor versions behind"
                    else:
                        detail = f"{latest_info[1] - pinned_info[1]} major versions behind"
                    status = f"[OUTDATED] ({detail})"
                    findings += 1
                elif gap > 0:
                    status = "[behind] (within policy)"
                else:
                    status = "[ok]"
        print(f"  {tool:<12} pinned {source.version:<12} latest {latest or '?':<10} {status}")

    used = _used_tools(root)
    for tool, where in sorted(used.items()):
        if tool not in sources:
            print(f"  {tool:<12} [MISSING] used ({where}) but not pinned in version config")
            findings += 1

    if (root / TOOL_VERSIONS_FILE).is_file() and not (root / MISE_FILE).is_file():
        print("[i] Hint: 'airlock migrate versions' can convert .tool-versions to mise.toml")
    return 1 if findings else 0


# --- update (Task 1.9) ----------------------------------------------------------


def _sandbox_dir_of(cwd: Path, home: Path | None) -> Path | None:
    """The sandbox directory containing cwd, or None when outside ~/sandboxes/."""
    root = sandbox_root(home).resolve()
    current = cwd.resolve()
    if not current.is_relative_to(root) or current == root:
        return None
    for candidate in [current, *current.parents]:
        if candidate.parent == root:
            return candidate
    return None


def _replace_tool_versions_line(path: Path, tool: str, new_version: str) -> str:
    """Rewrite the tool's line in a .tool-versions file; returns the old value."""
    pattern = re.compile(rf"^(?P<name>{re.escape(tool)})(?P<sep>\s+)(?P<rest>.*)$")
    old_version = ""
    out_lines: list[str] = []
    replaced = False
    for line in path.read_text(encoding="utf-8").splitlines(keepends=True):
        body = line.rstrip("\r\n")
        match = pattern.match(body)
        if match and not replaced:
            old_version = match.group("rest").strip()
            out_lines.append(f"{tool} {new_version}\n")
            replaced = True
        else:
            out_lines.append(line)
    if not replaced:
        raise VersioningError(f"cannot find '{tool}' in {path}")
    path.write_text("".join(out_lines), encoding="utf-8")
    return old_version


def _replace_mise_tools_value(path: Path, tool: str, new_version: str) -> str:
    """Rewrite the tool's value inside [tools] of a mise.toml; keeps the rest."""
    pattern = re.compile(
        rf"^(?P<pre>\s*\"?{re.escape(tool)}\"?\s*=\s*)(?P<value>[^#]+?)(?P<comment>\s+#.*)?$"
    )
    old_version = ""
    out_lines: list[str] = []
    replaced = False
    section = ""
    for line in path.read_text(encoding="utf-8").splitlines(keepends=True):
        body = line.rstrip("\r\n")
        header = re.match(r"\s*\[([^\]]+)\]", body)
        if header:
            section = header.group(1).strip()
        match = pattern.match(body) if section == "tools" and not replaced else None
        if match:
            old_version = match.group("value").strip().strip('"')
            out_lines.append(f"{match.group('pre')}\"{new_version}\"{match.group('comment') or ''}\n")
            replaced = True
        else:
            out_lines.append(line)
    if not replaced:
        raise VersioningError(f"cannot find '{tool}' in the [tools] section of {path}")
    path.write_text("".join(out_lines), encoding="utf-8")
    return old_version


def _detect_test_command(root: Path) -> list[str] | None:
    """First matching test runner: npm/pnpm/yarn, pytest, make, tests/run.sh."""
    package_json = root / "package.json"
    if package_json.is_file():
        try:
            scripts = json.loads(package_json.read_text(encoding="utf-8")).get("scripts", {})
        except (OSError, ValueError):
            scripts = {}
        if "test" in scripts:
            if (root / "pnpm-lock.yaml").is_file():
                return ["pnpm", "test"]
            if (root / "yarn.lock").is_file():
                return ["yarn", "test"]
            return ["npm", "test"]
    pyproject = root / "pyproject.toml"
    if pyproject.is_file():
        try:
            data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError):
            data = {}
        deps = " ".join(data.get("project", {}).get("dependencies", []) or [])
        if "pytest" in data.get("tool", {}) or "pytest" in deps:
            return ["pytest"]
    makefile = root / "Makefile"
    if makefile.is_file():
        try:
            for line in makefile.read_text(encoding="utf-8", errors="ignore").splitlines():
                if line.lstrip().startswith("test:"):
                    return ["make", "test"]
        except OSError:
            pass
    if (root / "tests" / "run.sh").is_file():
        return ["bash", "tests/run.sh"]
    return None


def update_tool(
    tool: str,
    *,
    project_root: Path | None = None,
    home: Path | None = None,
    latest_query: LatestQuery | None = None,
    test_cmd: list[str] | None = None,
) -> int:
    """Pin `tool` to 'mise latest' inside a sandbox, reinstall, run tests.

    Returns the test command's exit code (0 on success, non-zero on failure
    or when the update itself must abort). Changes stay uncommitted in the
    sandbox for `airlock merge`.
    """
    if latest_query is None:
        latest_query = _mise_latest
    cwd = project_root or Path.cwd()
    sandbox_dir = _sandbox_dir_of(cwd, home)
    if sandbox_dir is None:
        raise VersioningError(
            f"{cwd} is not inside a sandbox; run 'airlock update' from within "
            f"{sandbox_root(home)}"
        )

    sources = resolve_versions(sandbox_dir, start=cwd, warn=False)
    source = sources.get(tool)
    if source is None:
        raise VersioningError(
            f"tool '{tool}' is not pinned in the sandbox version config; add it first"
        )
    latest = latest_query(tool)
    if latest is None:
        raise VersioningError(
            f"cannot determine the latest version of '{tool}' (is mise installed?)"
        )

    if source.fmt == "mise":
        old_version = _replace_mise_tools_value(source.path, tool, latest)
    else:
        old_version = _replace_tool_versions_line(source.path, tool, latest)
    print(f"[i] {tool}: {old_version} -> {latest} (in {source.path})")

    try:
        proc = subprocess.run(
            ["mise", "install"], cwd=sandbox_dir, capture_output=True, text=True, check=False
        )
    except OSError:
        print("[!] 'mise' not found on PATH; skipping 'mise install'.", file=sys.stderr)
    else:
        if proc.stdout.strip():
            print(proc.stdout.strip())
        if proc.returncode != 0:
            print(
                f"[!] 'mise install' failed (exit {proc.returncode}); continuing to tests.",
                file=sys.stderr,
            )
        if proc.stderr.strip():
            print(proc.stderr.strip(), file=sys.stderr)

    cmd = test_cmd or _detect_test_command(sandbox_dir)
    if cmd is None:
        print("[i] No test command detected; skipping tests (changes left uncommitted).")
        return 0
    print(f"[i] Running tests: {' '.join(cmd)}")
    proc = subprocess.run(cmd, cwd=sandbox_dir, capture_output=True, text=True, check=False)
    if proc.stdout.strip():
        print(proc.stdout.strip())
    if proc.stderr.strip():
        print(proc.stderr.strip(), file=sys.stderr)
    if proc.returncode != 0:
        print(f"[!] Tests failed (exit {proc.returncode}); changes left uncommitted.")
        return proc.returncode or 1
    print("[i] Tests passed; changes left uncommitted for 'airlock merge'.")
    return 0


# --- migrate (Task 1.10) ----------------------------------------------------------


def parse_tool_versions_strict(text: str) -> list[tuple[str, str]]:
    """Strict parser for migration: only simple 'tool version' lines.

    Refuses 'path:'/'ref:' pseudo-versions, fallback lists (several versions
    on one line) and duplicate tools; all problems are reported at once.
    """
    entries: list[tuple[str, str]] = []
    seen: set[str] = set()
    errors: list[str] = []
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        tokens = line.split()
        if any(token.startswith(("path:", "ref:")) for token in tokens) or (
            len(tokens) > 2 and tokens[1] in ("path", "ref")
        ):
            errors.append(f"  line {lineno}: 'path:'/'ref:' syntax not supported: {line!r}")
            continue
        if len(tokens) == 1:
            errors.append(f"  line {lineno}: missing version: {line!r}")
            continue
        if len(tokens) > 2:
            errors.append(
                f"  line {lineno}: multiple versions (fallback list) not supported: {line!r}"
            )
            continue
        if tokens[0] in seen:
            errors.append(f"  line {lineno}: duplicate entry for '{tokens[0]}'")
            continue
        seen.add(tokens[0])
        entries.append((tokens[0], tokens[1]))
    if errors:
        raise VersioningError(
            f"{TOOL_VERSIONS_FILE} uses syntax that 'airlock migrate versions' does not "
            "support; migrate it by hand:\n" + "\n".join(errors)
        )
    if not entries:
        raise VersioningError(f"{TOOL_VERSIONS_FILE} has no tool entries; nothing to migrate")
    return entries


def render_mise_toml(entries: list[tuple[str, str]]) -> str:
    lines = [f"# Converted from {TOOL_VERSIONS_FILE} by 'airlock migrate versions'.", "[tools]"]
    for tool, version in entries:
        key = tool if re.fullmatch(r"[A-Za-z0-9_-]+", tool) else f'"{tool}"'
        lines.append(f'{key} = "{version}"')
    return "\n".join(lines) + "\n"


def migrate_versions(
    project_root: Path | None = None,
    *,
    dry_run: bool = False,
    force: bool = False,
    confirm: Confirm | None = None,
    home: Path | None = None,
) -> int:
    """Convert a simple .tool-versions into mise.toml. Narrow scope by design."""
    root = project_root or detect_project_root()
    if root is None:
        raise VersioningError("cannot detect the project root; cd into a project first")
    tool_versions = root / TOOL_VERSIONS_FILE
    mise = root / MISE_FILE
    if not tool_versions.is_file():
        raise VersioningError(f"no {TOOL_VERSIONS_FILE} in {root}; nothing to migrate")
    if mise.is_file():
        raise VersioningError(
            f"{mise} already exists; merging the two by hand is out of scope for migrate"
        )

    try:
        entries = parse_tool_versions_strict(tool_versions.read_text(encoding="utf-8"))
    except OSError as exc:
        raise VersioningError(f"cannot read {tool_versions}: {exc}") from exc
    content = render_mise_toml(entries)

    if dry_run:
        print(f"[i] dry run — would create {mise} with:")
        diff = difflib.unified_diff(
            [],
            content.splitlines(keepends=True),
            fromfile=f"{TOOL_VERSIONS_FILE} (current)",
            tofile=f"{MISE_FILE} (proposed)",
        )
        for line in diff:
            sys.stdout.write(line)
        return 0

    if confirm is None:
        if sys.stdin.isatty():
            print(f"Write {mise}? [y/N]: ", end="", flush=True)
            confirmed = sys.stdin.readline().strip().lower() in ("y", "yes")
        elif force:
            confirmed = True
        else:
            raise VersioningError(
                "non-interactive session without --force; re-run with --force to migrate"
            )
    else:
        confirmed = confirm(root)
    if not confirmed:
        print("[i] Aborted; no changes made.")
        return 1

    try:
        mise.write_text(content, encoding="utf-8")
    except OSError as exc:
        raise VersioningError(f"cannot write {mise}: {exc}") from exc
    print(f"[i] Wrote {mise}")
    print(
        f"[i] {tool_versions} kept as a backup; mise ignores it while {MISE_FILE} "
        "exists — remove it once the conversion is verified."
    )
    return 0
