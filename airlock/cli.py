import argparse
import os
import subprocess
import sys
from pathlib import Path

from airlock import __version__
from airlock.project import detect_project_root, looks_like_container
from airlock.sandbox import SandboxError, create_sandbox


def _sandbox_branch(sandbox_path: Path, project_name: str) -> str:
    """Derive the sandbox branch name from the sandbox directory name."""
    name = sandbox_path.name
    if not name.startswith(f"{project_name}_"):
        return f"sandbox/{name}"
    sandbox_id = name[len(project_name) + 1 :]
    # A uniqueness counter (_2, _3, ...) is appended on same-second collisions;
    # the branch stays sandbox/<timestamp>.
    parts = sandbox_id.split("_")
    if len(parts) == 3 and parts[2].isdigit():
        sandbox_id = f"{parts[0]}_{parts[1]}"
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

    shell = env.get("SHELL", "/bin/bash")
    subprocess.run([shell], cwd=sandbox_path, env=env)

    print("[i] Exited sandbox.")
    return 0


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

    args = parser.parse_args()

    if args.command == "enter":
        return cmd_enter(args.name)

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
