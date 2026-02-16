from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from difftrace.diff import (
    get_changed_files,
    get_git_root,
    map_files_to_packages,
    relativize_to_workspace,
)
from difftrace.graph import parse_lock_file
from difftrace.traverse import find_affected_packages


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="difftrace",
        description="Change detection for uv monorepos",
    )
    parser.add_argument(
        "--base",
        default="origin/main",
        help="Base ref to diff against (default: origin/main)",
    )
    parser.add_argument(
        "--json",
        dest="json_output",
        action="store_true",
        help="Output results as JSON",
    )
    parser.add_argument(
        "--lock-file",
        default="uv.lock",
        help="Path to uv.lock file (default: uv.lock)",
    )
    parser.add_argument(
        "--no-dev",
        action="store_true",
        help="Exclude dev dependencies from the dependency graph",
    )
    parser.add_argument(
        "--no-optional",
        action="store_true",
        help="Exclude optional dependencies from the dependency graph",
    )
    return parser


def run(args: argparse.Namespace) -> dict:
    """Orchestrate the full pipeline: parse → diff → map → BFS."""
    lock_path = Path(args.lock_file).resolve()
    workspace_root = lock_path.parent

    graph = parse_lock_file(
        lock_path,
        include_dev=not args.no_dev,
        include_optional=not args.no_optional,
    )

    git_root = get_git_root(cwd=workspace_root)
    changed_files = get_changed_files(args.base, repo_root=git_root)
    workspace_files = relativize_to_workspace(changed_files, git_root, workspace_root)

    directly_changed, test_all = map_files_to_packages(
        workspace_files, graph.packages
    )

    if test_all:
        affected = set(graph.packages.keys())
    else:
        affected = find_affected_packages(directly_changed, graph.reverse)

    return {
        "directly_changed": sorted(directly_changed),
        "affected": sorted(affected),
        "test_all": test_all,
    }


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    try:
        result = run(args)
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    if args.json_output:
        print(json.dumps(result))
    else:
        _print_human(result)


def _print_human(result: dict) -> None:
    directly_changed = set(result["directly_changed"])
    affected = result["affected"]
    test_all = result["test_all"]

    if test_all:
        print("Root config changed — testing all packages")
        print()

    if not affected:
        print("No affected packages.")
        return

    print(f"Affected packages ({len(affected)}):")
    for pkg in affected:
        marker = " (direct)" if pkg in directly_changed else " (transitive)"
        print(f"  - {pkg}{marker}")
