import argparse
import os
import subprocess
import sys
from pathlib import Path

from airlock import __version__
from airlock.git_wrapper import wrapper_bin_dir
from airlock.merge import MergeError, merge_sandbox
from airlock.project import detect_project_root, looks_like_container
from airlock.registry import (
    CleanError,
    clean_sandbox,
    format_sandbox_table,
    list_sandboxes,
    resolve_sandbox,
)
from airlock.sandbox import SandboxError, create_sandbox, parse_sandbox_name
from airlock.versioning import (
    VersioningError,
    check_versions,
    migrate_versions,
    update_tool,
)


def _sandbox_branch(sandbox_path: Path, project_name: str) -> str:
    """Derive the sandbox branch name from the sandbox directory name."""
    name = sandbox_path.name
    if not name.startswith(f"{project_name}_"):
        return f"sandbox/{name}"
    try:
        _, sandbox_id = parse_sandbox_name(name)
    except SandboxError:
        # Manually renamed directory: keep the old lenient behaviour.
        return f"sandbox/{name[len(project_name) + 1 :]}"
    # A uniqueness counter (_2, _3, ...) is appended on same-second collisions;
    # the branch stays sandbox/<timestamp>.
    return f"sandbox/{sandbox_id}"


def cmd_enter(name: str | None) -> int:
    """Create a sandbox for the current project and enter a subshell in it."""
    if name:
        # Explicit names need the registry lookup (Task 1.6).
        print("[err] Explicit project names are not supported yet.", file=sys.stderr)
        print("      cd into a project directory instead.", file=sys.stderr)
        return 1

    root = detect_project_root()
    if root is None:
        print("[err] Cannot detect project root.", file=sys.stderr)
        print("      Run 'airlock enter <name>' or cd into a project directory.", file=sys.stderr)
        return 1

    is_container, details = looks_like_container(root)

    if is_container:
        print(f"[!] '{root.name}' looks like a container, not a project.", file=sys.stderr)
        print(f"    Root: {root}", file=sys.stderr)
        if details["nested_projects"]:
            print("    Found nested projects:", file=sys.stderr)
            for p in details["nested_projects"]:
                print(f"      - {p}", file=sys.stderr)
        print(f"    Size: {details['size_mb']:.0f} MB", file=sys.stderr)
        print(f"    Reasons: {', '.join(details['reasons'])}", file=sys.stderr)
        print(file=sys.stderr)
        print("    Creating a sandbox will clone the ENTIRE directory.", file=sys.stderr)
        print("    Continue anyway? [y/N]: ", end="", file=sys.stderr, flush=True)

        answer = sys.stdin.readline().strip().lower()
        if answer not in ("y", "yes"):
            print("[i] Aborted.", file=sys.stderr)
            return 1

    try:
        sandbox_path = create_sandbox(root, root.name)
    except SandboxError as e:
        print(f"[err] Sandbox creation failed: {e}", file=sys.stderr)
        return 1

    print(f"[i] Project: {root.name}")
    print(f"[i] Sandbox: {sandbox_path}")
    print(f"[i] Branch: {_sandbox_branch(sandbox_path, root.name)}")
    print("[i] Entering subshell. Type 'exit' to return.")

    env = os.environ.copy()
    env["AIRLOCK_ACTIVE"] = "1"
    env["AIRLOCK_MAIN"] = str(root)
    env["PS1"] = f"(airlock:{root.name}) \\w $ "
    # Guarded git wrapper (Task 1.4): shadow `git` inside the sandbox so the
    # push/checkout/config guardrails apply before the real git runs.
    env["PATH"] = f"{wrapper_bin_dir()}{os.pathsep}{env.get('PATH', '')}"

    shell = env.get("SHELL", "/bin/bash")
    subprocess.run([shell], cwd=sandbox_path, env=env)

    print("[i] Exited sandbox.")
    return 0


def _current_project_name() -> str | None:
    root = detect_project_root()
    return root.name if root is not None else None


def cmd_merge(name: str | None) -> int:
    """Merge a sandbox branch into the current (main) project."""
    selected = resolve_sandbox(name, current_project=_current_project_name())
    if selected is None:
        return 1
    try:
        return merge_sandbox(selected.name)
    except MergeError as exc:
        print(f"[err] {exc}", file=sys.stderr)
        return 1


def cmd_list() -> int:
    """Print all sandboxes as an aligned table."""
    infos, skipped = list_sandboxes()
    if skipped:
        print(f"[!] {skipped} unrecognized entries in ~/sandboxes/ not listed.", file=sys.stderr)
    if not infos:
        print("[i] No sandboxes.")
        return 0
    print(format_sandbox_table(infos))
    return 0


def cmd_clean(name: str | None, force: bool = False) -> int:
    """Delete a sandbox after strict checks (or --force + typed 'yes')."""
    selected = resolve_sandbox(name, current_project=_current_project_name())
    if selected is None:
        return 1
    try:
        return clean_sandbox(selected.name, force=force)
    except CleanError as exc:
        print(f"[err] {exc}", file=sys.stderr)
        return 1


def cmd_check() -> int:
    """Compare pinned versions with 'mise latest'; report coverage gaps."""
    root = detect_project_root()
    if root is None:
        print("[err] Cannot detect project root.", file=sys.stderr)
        return 1
    try:
        return check_versions(root)
    except VersioningError as exc:
        print(f"[err] {exc}", file=sys.stderr)
        return 1


def cmd_update(tool: str) -> int:
    """Update a pinned tool to 'mise latest' (sandbox only)."""
    try:
        return update_tool(tool)
    except VersioningError as exc:
        print(f"[err] {exc}", file=sys.stderr)
        return 1


def cmd_migrate(dry_run: bool = False, force: bool = False) -> int:
    """Convert .tool-versions to mise.toml (narrow, strict scope)."""
    try:
        return migrate_versions(dry_run=dry_run, force=force)
    except VersioningError as exc:
        print(f"[err] {exc}", file=sys.stderr)
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="airlock",
        description="Dev-sandbox isolation for internal tools",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"airlock {__version__}",
    )

    subparsers = parser.add_subparsers(dest="command", metavar="<command>")

    enter = subparsers.add_parser("enter", help="Enter an isolated sandbox for the current project")
    enter.add_argument("name", nargs="?", help="Project name (optional)")

    merge = subparsers.add_parser("merge", help="Merge a sandbox branch into the main project")
    merge.add_argument("sandbox", nargs="?", help="Sandbox name or partial id (omit to pick)")

    subparsers.add_parser("list", help="List sandboxes with branch and status")

    clean = subparsers.add_parser(
        "clean", help="Delete a sandbox (refuses when dirty or unpushed)"
    )
    clean.add_argument("sandbox", nargs="?", help="Sandbox name or partial id (omit to pick)")
    clean.add_argument(
        "--force",
        action="store_true",
        help="Bypass dirty/unpushed checks (still requires typing 'yes')",
    )

    subparsers.add_parser("check", help="Check pinned versions against 'mise latest'")

    update = subparsers.add_parser(
        "update", help="Update a pinned tool to 'mise latest' (sandbox only)"
    )
    update.add_argument("tool", help="Tool to update, e.g. python")

    migrate = subparsers.add_parser(
        "migrate", help="Convert .tool-versions to mise.toml (narrow scope)"
    )
    migrate.add_argument("target", choices=("versions",), help="What to migrate")
    migrate.add_argument(
        "--dry-run", action="store_true", help="Print the would-be mise.toml and exit"
    )
    migrate.add_argument(
        "--force", action="store_true", help="Skip the confirmation prompt (non-interactive)"
    )

    args = parser.parse_args()

    if args.command == "enter":
        return cmd_enter(args.name)
    if args.command == "merge":
        return cmd_merge(args.sandbox)
    if args.command == "list":
        return cmd_list()
    if args.command == "clean":
        return cmd_clean(args.sandbox, force=args.force)
    if args.command == "check":
        return cmd_check()
    if args.command == "update":
        return cmd_update(args.tool)
    if args.command == "migrate":
        return cmd_migrate(dry_run=args.dry_run, force=args.force)

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
