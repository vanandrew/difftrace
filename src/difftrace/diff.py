from __future__ import annotations

import fnmatch
import logging
import subprocess
from pathlib import Path

from difftrace.graph import WorkspacePackage

logger = logging.getLogger(__name__)

# Default files/dirs at the workspace root that trigger testing all packages.
DEFAULT_ROOT_TRIGGERS = {"pyproject.toml", "uv.lock"}
DEFAULT_DIR_TRIGGERS = {".github/"}


def get_git_root(cwd: Path | None = None) -> Path:
    """Get the git repository root directory."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            cwd=cwd,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError("git command timed out after 30 seconds")
    if result.returncode != 0:
        raise ValueError("Not a git repository. Run difftrace from within a git repo.")
    logger.debug("Git root: %s", result.stdout.strip())
    return Path(result.stdout.strip())


def get_changed_files(base_ref: str, repo_root: Path | None = None) -> list[str]:
    """Get list of files changed between base_ref and HEAD.

    Returns paths relative to the git root.
    """
    if not base_ref or not base_ref.strip():
        raise ValueError("base_ref must not be empty")
    if "\x00" in base_ref:
        raise ValueError("base_ref must not contain null bytes")
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", f"{base_ref}...HEAD"],
            capture_output=True,
            text=True,
            cwd=repo_root,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError("git command timed out after 30 seconds")
    if result.returncode != 0:
        stderr = result.stderr.strip()
        if "unknown revision" in stderr or "not a git repository" in stderr:
            msg = (
                f"Could not resolve ref '{base_ref}'. "
                "Does the branch/ref exist? "
                "Try 'git fetch' or use --base with a valid ref."
            )
            if "unknown revision" in stderr:
                msg += "\nIf running in CI, ensure you checkout with fetch-depth: 0."
            raise ValueError(msg)
        raise RuntimeError(f"git diff failed: {stderr}")
    files = [f for f in result.stdout.strip().splitlines() if f]
    logger.debug("Changed files (%d): %s", len(files), files)
    return files


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
    *,
    root_triggers: set[str] | None = None,
    dir_triggers: set[str] | None = None,
) -> tuple[set[str], bool]:
    """Map workspace-relative changed files to affected packages.

    Args:
        changed_files: File paths relative to the workspace root.
        packages: Workspace packages from the dependency graph.
        root_triggers: File names that trigger test_all. None uses defaults.
        dir_triggers: Directory prefixes that trigger test_all. None uses defaults.

    Returns:
        Tuple of (directly changed package names, test_all flag).
    """
    if root_triggers is None:
        root_triggers = DEFAULT_ROOT_TRIGGERS
    if dir_triggers is None:
        dir_triggers = DEFAULT_DIR_TRIGGERS

    test_all = False
    directly_changed: set[str] = set()

    # Sort packages by source_path length descending for longest-prefix match
    sorted_packages = sorted(
        packages.values(),
        key=lambda p: len(p.source_path),
        reverse=True,
    )

    glob_triggers = {t for t in root_triggers if any(c in t for c in "*?[")}
    exact_triggers = root_triggers - glob_triggers

    for filepath in changed_files:
        # Check root triggers (exact match then glob)
        if filepath in exact_triggers:
            test_all = True
            continue

        if glob_triggers and any(fnmatch.fnmatch(filepath, t) for t in glob_triggers):
            test_all = True
            continue

        if any(filepath.startswith(trigger) for trigger in dir_triggers):
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
