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
    route_files_to_workspaces,
)
from difftrace.graph import Workspace, load_workspaces
from difftrace.traverse import find_affected_packages

logger = logging.getLogger(__name__)

# Keys that should not appear in JSON output.
_INTERNAL_KEYS = {
    "packages",
    "changed_files",
    "file_mapping",
    "_workspaces",
    "_ws_labels",
    "_is_multi",
    "_root_level_files",
}


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
        action="append",
        default=None,
        help=(
            "Path to a uv.lock file (default: uv.lock). "
            "Repeat for multi-workspace monorepos."
        ),
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
        "--test-all",
        action="store_true",
        help="Force testing all packages, skipping git diff entirely",
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


def _normalize_lock_arg(raw: object) -> list[Path]:
    """Normalize --lock-file into a list of Paths.

    Accepts None (default), a single string, or a list of strings.
    """
    if not raw:
        return [Path("uv.lock")]
    if isinstance(raw, str):
        return [Path(raw)]
    return [Path(p) for p in raw]


def _workspace_label(ws: Workspace, git_root: Path | None) -> str:
    """Compute a workspace's git-root-relative label ("" for the root itself)."""
    if git_root is None:
        return ""
    try:
        rel = str(ws.workspace_root.resolve().relative_to(git_root))
    except ValueError:
        return str(ws.workspace_root)
    return "" if rel == "." else rel


def _qualify(label: str, name: str) -> str:
    return f"{label}/{name}" if label else name


def _source_display_path(label: str, source_path: str) -> str:
    """Return a source_path joined with its workspace label for display."""
    if not label:
        return source_path
    if source_path == ".":
        return label
    return f"{label}/{source_path}"


def run(args: argparse.Namespace) -> dict:
    """Orchestrate the full pipeline: parse -> diff -> route -> map -> BFS."""
    lock_paths = _normalize_lock_arg(args.lock_file)
    workspaces = load_workspaces(
        lock_paths,
        include_dev=not args.no_dev,
        include_optional=not args.no_optional,
    )
    is_multi = len(workspaces) > 1

    virtual_roots: list[set[str]] = [
        {name for name, pkg in ws.graph.packages.items() if pkg.source_path == "."}
        for ws in workspaces
    ]
    exclude_set = set(args.exclude or [])

    git_root: Path | None = None
    changed_files: list[str] = []
    ws_files: list[list[str]] = [[] for _ in workspaces]
    root_level_files: list[str] = []

    if args.test_all:
        test_all = True
        ws_directly: list[set[str]] = [set() for _ in workspaces]
        ws_affected: list[set[str]] = [
            set(ws.graph.packages.keys()) - virtual_roots[i]
            for i, ws in enumerate(workspaces)
        ]
    else:
        git_root = get_git_root(cwd=workspaces[0].workspace_root).resolve()
        changed_files = get_changed_files(args.base, repo_root=git_root)
        ws_files, root_level_files = route_files_to_workspaces(
            changed_files, git_root, workspaces
        )

        root_triggers, dir_triggers = _parse_triggers(args.root_trigger)

        # Global test_all: any changed file at the git root that matches a root
        # trigger. This keeps today's single-lock-at-git-root behavior intact
        # (workspace root == git root → workspace's own pyproject.toml/uv.lock
        # is a git-root-level trigger).
        _, test_all = map_files_to_packages(
            changed_files,
            {},
            root_triggers=root_triggers,
            dir_triggers=dir_triggers,
        )

        ws_directly = []
        for i, ws in enumerate(workspaces):
            directly, ws_test_all = map_files_to_packages(
                ws_files[i],
                ws.graph.packages,
                root_triggers=root_triggers,
                dir_triggers=dir_triggers,
            )
            if ws_test_all:
                directly = set(ws.graph.packages.keys()) - virtual_roots[i]
                # Single-lock legacy: a workspace-relative trigger (e.g. a
                # nested workspace's own pyproject.toml/uv.lock) also sets the
                # global test_all flag. In multi-lock, this stays workspace-
                # scoped so one sub-workspace's config change doesn't force a
                # full test run across every sibling workspace.
                if not is_multi:
                    test_all = True
            ws_directly.append(directly)

        ws_affected = []
        for i, ws in enumerate(workspaces):
            if args.direct_only:
                aff = ws_directly[i] - virtual_roots[i]
            elif test_all:
                aff = set(ws.graph.packages.keys()) - virtual_roots[i]
            else:
                aff = (
                    find_affected_packages(ws_directly[i], ws.graph.reverse)
                    - virtual_roots[i]
                )
            ws_affected.append(aff)

    ws_directly = [d - exclude_set for d in ws_directly]
    ws_affected = [a - exclude_set for a in ws_affected]

    # Labels are needed for qualified output and --paths/--names in multi-lock.
    if is_multi and git_root is None:
        git_root = get_git_root(cwd=workspaces[0].workspace_root).resolve()
    ws_labels = [_workspace_label(ws, git_root) for ws in workspaces]

    if is_multi:
        directly_out: list = sorted(
            [
                {"name": n, "workspace": ws_labels[i]}
                for i, d in enumerate(ws_directly)
                for n in d
            ],
            key=lambda e: (e["workspace"], e["name"]),
        )
        affected_out: list = sorted(
            [
                {"name": n, "workspace": ws_labels[i]}
                for i, a in enumerate(ws_affected)
                for n in a
            ],
            key=lambda e: (e["workspace"], e["name"]),
        )
    else:
        directly_out = sorted(ws_directly[0])
        affected_out = sorted(ws_affected[0])

    file_mapping: dict[str, str | None] = {}
    if args.detailed and not args.test_all:
        for i, ws in enumerate(workspaces):
            sorted_pkgs = sorted(
                ws.graph.packages.values(),
                key=lambda p: len(p.source_path),
                reverse=True,
            )
            label = ws_labels[i]
            for wf in ws_files[i]:
                matched: str | None = None
                for pkg in sorted_pkgs:
                    if pkg.source_path == ".":
                        continue
                    if wf.startswith(pkg.source_path + "/"):
                        matched = _qualify(label, pkg.name) if is_multi else pkg.name
                        break
                key = wf
                if is_multi and label:
                    key = f"{label}/{wf}" if wf != "." else label
                file_mapping[key] = matched
        if is_multi:
            for f in root_level_files:
                file_mapping[f] = None

    result: dict = {
        "directly_changed": directly_out,
        "affected": affected_out,
        "test_all": test_all,
        "changed_files": changed_files if is_multi else ws_files[0],
        "file_mapping": file_mapping,
        "_workspaces": workspaces,
        "_ws_labels": ws_labels,
        "_is_multi": is_multi,
        "_root_level_files": root_level_files,
    }
    if is_multi:
        result["workspaces"] = ws_labels
    else:
        result["packages"] = workspaces[0].graph.packages
    return result


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

    if args.json_output:
        out = {k: v for k, v in result.items() if k not in _INTERNAL_KEYS}
        if args.detailed:
            out["file_mapping"] = result["file_mapping"]
        print(json.dumps(out))
    elif args.names:
        _print_names(result)
    elif args.paths:
        _print_paths(result)
    else:
        _print_human(result, detailed=args.detailed)


def _print_names(result: dict) -> None:
    is_multi = result.get("_is_multi", False)
    for entry in result["affected"]:
        if is_multi:
            print(_qualify(entry["workspace"], entry["name"]))
        else:
            print(entry)


def _print_paths(result: dict) -> None:
    is_multi = result.get("_is_multi", False)
    if is_multi:
        workspaces: list[Workspace] = result["_workspaces"]
        labels: list[str] = result["_ws_labels"]
        lookup: dict[tuple[str, str], str] = {}
        for i, ws in enumerate(workspaces):
            label = labels[i]
            for name, pkg in ws.graph.packages.items():
                lookup[(label, name)] = _source_display_path(label, pkg.source_path)
        for entry in result["affected"]:
            print(lookup[(entry["workspace"], entry["name"])])
    else:
        packages = result["packages"]
        for name in result["affected"]:
            print(packages[name].source_path)


def _print_human(result: dict, *, detailed: bool = False) -> None:
    is_multi = result.get("_is_multi", False)
    affected = result["affected"]
    test_all = result["test_all"]

    if is_multi:
        directly_keys = {
            (e["workspace"], e["name"]) for e in result["directly_changed"]
        }
    else:
        directly_keys = set(result["directly_changed"])

    if test_all:
        print("Testing all packages")
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
    for entry in affected:
        if is_multi:
            qualified = _qualify(entry["workspace"], entry["name"])
            is_direct = (entry["workspace"], entry["name"]) in directly_keys
        else:
            qualified = entry
            is_direct = entry in directly_keys
        marker = " (direct)" if is_direct else " (transitive)"
        print(f"  - {qualified}{marker}")
