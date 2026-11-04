import argparse
import sys
from pathlib import Path

from airlock import __version__
from airlock.project import detect_project_root, looks_like_container


def cmd_enter(name: str | None) -> int:
    """Task 1.2: dry-run. Detect project, warn on containers, do not create sandbox."""
    if name:
        print(f"[i] Project: {name} (explicit)")
        print("    (dry-run: sandbox not created yet)")
        return 0

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

    print(f"[i] Detected project: {root.name}")
    print(f"    Root: {root}")
    print("    (dry-run: sandbox not created yet)")
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

    enter = subparsers.add_parser("enter", help="Enter a project sandbox (dry-run in 1.2)")
    enter.add_argument("name", nargs="?", help="Project name (optional)")

    args = parser.parse_args()

    if args.command == "enter":
        return cmd_enter(args.name)

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
