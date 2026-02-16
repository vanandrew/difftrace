from __future__ import annotations

import tomllib
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class WorkspacePackage:
    name: str
    source_path: str
    dependencies: list[str] = field(default_factory=list)
    optional_dependencies: dict[str, list[str]] = field(default_factory=dict)
    dev_dependencies: dict[str, list[str]] = field(default_factory=dict)


@dataclass
class DependencyGraph:
    packages: dict[str, WorkspacePackage] = field(default_factory=dict)
    forward: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    reverse: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))


def _extract_dep_names(deps: list[dict], members: set[str]) -> list[str]:
    """Extract dependency names that are workspace members."""
    result = []
    for dep in deps:
        name = dep.get("name", "")
        if name in members:
            result.append(name)
    return result


def _get_source_path(source: dict) -> str | None:
    """Extract source path from a package source entry."""
    for key in ("editable", "directory", "virtual"):
        if key in source:
            return source[key]
    return None


def parse_lock_file(
    lock_path: Path,
    *,
    include_dev: bool = True,
    include_optional: bool = True,
) -> DependencyGraph:
    """Parse a uv.lock file and build the workspace dependency graph.

    Args:
        lock_path: Path to the uv.lock file.
        include_dev: Whether to include dev dependencies in the graph.
        include_optional: Whether to include optional dependencies in the graph.

    Returns:
        A DependencyGraph with forward and reverse edges for workspace packages.

    Raises:
        FileNotFoundError: If the lock file doesn't exist.
        ValueError: If the lock file has no [manifest] section (not a workspace).
    """
    data = tomllib.loads(lock_path.read_text())

    manifest = data.get("manifest")
    if manifest is None:
        raise ValueError(
            f"{lock_path} has no [manifest] section — is this a uv workspace?"
        )

    members = set(manifest.get("members", []))
    if not members:
        raise ValueError(f"{lock_path} has no workspace members in [manifest]")

    graph = DependencyGraph()

    for pkg_data in data.get("package", []):
        name = pkg_data.get("name", "")
        if name not in members:
            continue

        source = pkg_data.get("source", {})
        source_path = _get_source_path(source)
        if source_path is None:
            continue

        raw_deps = pkg_data.get("dependencies", [])
        deps = _extract_dep_names(raw_deps, members)

        optional_deps: dict[str, list[str]] = {}
        for group_name, group_deps in pkg_data.get("optional-dependencies", {}).items():
            filtered = _extract_dep_names(group_deps, members)
            if filtered:
                optional_deps[group_name] = filtered

        dev_deps: dict[str, list[str]] = {}
        for group_name, group_deps in pkg_data.get("dev-dependencies", {}).items():
            filtered = _extract_dep_names(group_deps, members)
            if filtered:
                dev_deps[group_name] = filtered

        package = WorkspacePackage(
            name=name,
            source_path=source_path,
            dependencies=deps,
            optional_dependencies=optional_deps,
            dev_dependencies=dev_deps,
        )
        graph.packages[name] = package

    # Build forward and reverse edges
    for name, pkg in graph.packages.items():
        for dep in pkg.dependencies:
            graph.forward[name].add(dep)

        if include_optional:
            for group_deps in pkg.optional_dependencies.values():
                for dep in group_deps:
                    graph.forward[name].add(dep)

        if include_dev:
            for group_deps in pkg.dev_dependencies.values():
                for dep in group_deps:
                    graph.forward[name].add(dep)

    # Build reverse edges from forward
    for name, deps in graph.forward.items():
        for dep in deps:
            graph.reverse[dep].add(name)

    return graph
