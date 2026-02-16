from __future__ import annotations

import subprocess
from pathlib import Path

from difftrace.graph import WorkspacePackage

# Files at the workspace root that should trigger testing all packages.
ROOT_TRIGGERS = {"pyproject.toml", "uv.lock"}
DIR_TRIGGERS = {".github/"}


def get_git_root(cwd: Path | None = None) -> Path:
    """Get the git repository root directory."""
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=True,
        cwd=cwd,
    )
    return Path(result.stdout.strip())


def get_changed_files(base_ref: str, repo_root: Path | None = None) -> list[str]:
    """Get list of files changed between base_ref and HEAD.

    Returns paths relative to the git root.
    """
    result = subprocess.run(
        ["git", "diff", "--name-only", f"{base_ref}...HEAD"],
        capture_output=True,
        text=True,
        check=True,
        cwd=repo_root,
    )
    return [f for f in result.stdout.strip().splitlines() if f]


def relativize_to_workspace(
    changed_files: list[str],
    git_root: Path,
    workspace_root: Path,
) -> list[str]:
    """Convert git-root-relative paths to workspace-root-relative paths.

    Files outside the workspace are dropped.
    """
    workspace_root = workspace_root.resolve()
    git_root = git_root.resolve()

    if workspace_root == git_root:
        return changed_files

    try:
        prefix = str(workspace_root.relative_to(git_root))
    except ValueError:
        return []

    prefix_with_slash = prefix + "/"
    result = []
    for f in changed_files:
        if f.startswith(prefix_with_slash):
            result.append(f[len(prefix_with_slash) :])
        elif f == prefix:
            result.append(".")
    return result


def map_files_to_packages(
    changed_files: list[str],
    packages: dict[str, WorkspacePackage],
) -> tuple[set[str], bool]:
    """Map workspace-relative changed files to affected packages.

    Args:
        changed_files: File paths relative to the workspace root.
        packages: Workspace packages from the dependency graph.

    Returns:
        Tuple of (directly changed package names, test_all flag).
    """
    test_all = False
    directly_changed: set[str] = set()

    # Sort packages by source_path length descending for longest-prefix match
    sorted_packages = sorted(
        packages.values(),
        key=lambda p: len(p.source_path),
        reverse=True,
    )

    for filepath in changed_files:
        # Check root triggers
        if filepath in ROOT_TRIGGERS:
            test_all = True
            continue

        if any(filepath.startswith(trigger) for trigger in DIR_TRIGGERS):
            test_all = True
            continue

        # Try to match to a package
        for pkg in sorted_packages:
            # Skip virtual root packages to avoid matching everything
            if pkg.source_path == ".":
                continue
            if filepath.startswith(pkg.source_path + "/"):
                directly_changed.add(pkg.name)
                break

    return directly_changed, test_all
