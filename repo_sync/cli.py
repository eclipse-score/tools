# src/tools/repo_sync/cli.py

import argparse
from pathlib import Path
from collections.abc import Sequence


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="repo-sync",
        description="Synchronize files and file sections between repositories.",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    apply_parser = subparsers.add_parser(
        "apply",
        help="Apply synchronization rules to a target repository.",
    )
    apply_parser.add_argument(
        "--config",
        type=Path,
        required=True,
        help="Path to the synchronization configuration.",
    )
    apply_parser.add_argument(
        "--source-root",
        type=Path,
        required=True,
        help="Root directory of the source repository.",
    )
    apply_parser.add_argument(
        "--target-root",
        type=Path,
        required=True,
        help="Root directory of the target repository.",
    )

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    print("Starting repo-sync...")
    parser = create_parser()
    args = parser.parse_args(argv)

    if args.command == "apply":
        print(
            f"Applying {args.config} from "
            f"{args.source_root} to {args.target_root}"
        )
        return 0

    parser.error(f"Unsupported command: {args.command}")
