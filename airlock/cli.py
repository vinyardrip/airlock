import argparse
from airlock import __version__


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
    parser.parse_args()
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
