from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
from pathlib import Path

from difftrace import __version__
from difftrace.diff import (
    DEFAULT_DIR_TRIGGERS,
    DEFAULT_ROOT_TRIGGERS,
    get_changed_files,
    get_git_root,
    map_files_to_packages,
    relativize_to_workspace,
)
from difftrace.graph import parse_lock_file
from difftrace.traverse import find_affected_packages

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="difftrace",
        description="Change detection for uv monorepos",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    parser.add_argument(
        "--base",
        default="origin/main",
        help="Base ref to diff against (default: origin/main)",
    )
    output_group = parser.add_mutually_exclusive_group()
    output_group.add_argument(
        "--json",
        dest="json_output",
        action="store_true",
        help="Output results as JSON",
    )
    output_group.add_argument(
        "--names",
        action="store_true",
        help="Output affected package names, one per line",
    )
    output_group.add_argument(
        "--paths",
        action="store_true",
        help="Output affected package source paths, one per line",
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
    parser.add_argument(
        "--direct-only",
        action="store_true",
        help="Only output directly changed packages, skip transitive deps",
    )
    parser.add_argument(
        "--detailed",
        action="store_true",
        help="Show changed files and their package mapping",
    )
    parser.add_argument(
        "--root-trigger",
        action="append",
        metavar="PATTERN",
        help=(
            "Additional file/dir patterns that trigger test_all "
            "(e.g. --root-trigger Dockerfile --root-trigger docker/). "
            "Append a / for directory prefixes. "
            "Built-in triggers: pyproject.toml, uv.lock, .github/"
        ),
    )
    parser.add_argument(
        "--exclude",
        action="append",
        metavar="PACKAGE",
        help="Exclude a package from the affected set (repeatable)",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable verbose/debug logging to stderr",
    )
    return parser


def _parse_triggers(
    extra: list[str] | None,
) -> tuple[set[str], set[str]]:
    """Split extra trigger patterns into file triggers and dir triggers."""
    root_triggers = set(DEFAULT_ROOT_TRIGGERS)
    dir_triggers = set(DEFAULT_DIR_TRIGGERS)
    for pattern in extra or []:
        if pattern.endswith("/"):
            dir_triggers.add(pattern)
        else:
            root_triggers.add(pattern)
    return root_triggers, dir_triggers


def run(args: argparse.Namespace) -> dict:
    """Orchestrate the full pipeline: parse -> diff -> map -> BFS."""
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

    root_triggers, dir_triggers = _parse_triggers(args.root_trigger)

    directly_changed, test_all = map_files_to_packages(
        workspace_files,
        graph.packages,
        root_triggers=root_triggers,
        dir_triggers=dir_triggers,
    )

    # Filter out virtual root packages — they have no code/tests to run
    virtual_roots = {
        name for name, pkg in graph.packages.items() if pkg.source_path == "."
    }

    exclude_set = set(args.exclude or [])

    if args.direct_only:
        affected = directly_changed - virtual_roots
    elif test_all:
        affected = set(graph.packages.keys()) - virtual_roots
    else:
        affected = (
            find_affected_packages(directly_changed, graph.reverse) - virtual_roots
        )

    directly_changed -= exclude_set
    affected -= exclude_set

    # Build file-to-package mapping for --detailed
    file_mapping: dict[str, str | None] = {}
    if args.detailed:
        sorted_packages = sorted(
            graph.packages.values(),
            key=lambda p: len(p.source_path),
            reverse=True,
        )
        for filepath in workspace_files:
            matched = None
            for pkg in sorted_packages:
                if pkg.source_path == ".":
                    continue
                if filepath.startswith(pkg.source_path + "/"):
                    matched = pkg.name
                    break
            file_mapping[filepath] = matched

    return {
        "directly_changed": sorted(directly_changed),
        "affected": sorted(affected),
        "test_all": test_all,
        "packages": graph.packages,
        "changed_files": workspace_files,
        "file_mapping": file_mapping,
    }


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        stream=sys.stderr,
        format="%(name)s: %(message)s",
    )

    try:
        result = run(args)
    except (
        FileNotFoundError,
        ValueError,
        RuntimeError,
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
        OSError,
    ) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    internal_keys = {"packages", "changed_files", "file_mapping"}

    if args.json_output:
        out = {k: v for k, v in result.items() if k not in internal_keys}
        if args.detailed:
            out["file_mapping"] = result["file_mapping"]
        print(json.dumps(out))
    elif args.names:
        for name in result["affected"]:
            print(name)
    elif args.paths:
        packages = result["packages"]
        for name in result["affected"]:
            print(packages[name].source_path)
    else:
        _print_human(result, detailed=args.detailed)


def _print_human(result: dict, *, detailed: bool = False) -> None:
    directly_changed = set(result["directly_changed"])
    affected = result["affected"]
    test_all = result["test_all"]

    if test_all:
        print("Root config changed — testing all packages")
        print()

    if detailed:
        file_mapping = result["file_mapping"]
        print(f"Changed files ({len(file_mapping)}):")
        for filepath, pkg in sorted(file_mapping.items()):
            label = pkg if pkg else "(root/unmatched)"
            print(f"  {filepath}  -> {label}")
        print()

    if not affected:
        print("No affected packages.")
        return

    print(f"Affected packages ({len(affected)}):")
    for pkg in affected:
        marker = " (direct)" if pkg in directly_changed else " (transitive)"
        print(f"  - {pkg}{marker}")
