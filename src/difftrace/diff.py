from __future__ import annotations

import fnmatch
import logging
import os
import subprocess
from pathlib import Path

from difftrace.graph import Workspace, WorkspacePackage

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
    except subprocess.TimeoutExpired as e:
        raise RuntimeError("git command timed out after 30 seconds") from e
    if result.returncode != 0:
        raise ValueError("Not a git repository. Run difftrace from within a git repo.")
    logger.debug("Git root: %s", result.stdout.strip())
    return Path(result.stdout.strip())


def _resolve_sha(ref: str, cwd: Path | None = None) -> str | None:
    """Resolve a git ref to its SHA, or None if it can't be resolved."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--verify", ref],
            capture_output=True,
            text=True,
            cwd=cwd,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def get_changed_files(base_ref: str, repo_root: Path | None = None) -> list[str]:
    """Get list of files changed between base_ref and HEAD.

    Returns paths relative to the git root.
    """
    if not base_ref or not base_ref.strip():
        raise ValueError("base_ref must not be empty")
    if "\x00" in base_ref:
        raise ValueError("base_ref must not contain null bytes")

    # Warn if base_ref and HEAD resolve to the same commit (empty diff).
    base_sha = _resolve_sha(base_ref, cwd=repo_root)
    head_sha = _resolve_sha("HEAD", cwd=repo_root)
    if base_sha and head_sha and base_sha == head_sha:
        logger.warning(
            "Base ref '%s' and HEAD resolve to the same commit (%s). "
            "Diff will be empty. In CI push workflows, use the pre-push SHA "
            "(e.g. --base ${{ github.event.before }}) instead of origin/main.",
            base_ref,
            base_sha[:12],
        )

    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", f"{base_ref}...HEAD"],
            capture_output=True,
            text=True,
            cwd=repo_root,
            timeout=30,
        )
    except subprocess.TimeoutExpired as e:
        raise RuntimeError("git command timed out after 30 seconds") from e
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


def normalize_extensions(raw: list[str] | None) -> set[str]:
    """Normalize a list of extension strings into a lowercase, dot-prefixed set.

    Accepts ``md``, ``.md``, or ``MD`` and yields ``.md``. Empty strings and
    whitespace-only entries are dropped.
    """
    out: set[str] = set()
    for ext in raw or []:
        ext = ext.strip().lower()
        if not ext:
            continue
        if not ext.startswith("."):
            ext = "." + ext
        out.add(ext)
    return out


def filter_excluded_extensions(files: list[str], excluded: set[str]) -> list[str]:
    """Drop files whose extension matches one of the excluded extensions.

    Matches the final suffix only (``foo.tar.gz`` → ``.gz``).
    Comparison is case-insensitive.
    """
    if not excluded:
        return files
    return [f for f in files if os.path.splitext(f)[1].lower() not in excluded]


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


def route_files_to_workspaces(
    changed_files: list[str],
    git_root: Path,
    workspaces: list[Workspace],
) -> tuple[list[list[str]], list[str]]:
    """Route git-root-relative files to the workspace whose root is the
    longest matching prefix.

    Returns:
        A pair ``(per_workspace_files, root_level_files)``:
          - ``per_workspace_files[i]`` holds the files routed to
            ``workspaces[i]``, relativized to that workspace's root.
          - ``root_level_files`` holds any file that matched no workspace
            root (still git-root-relative).
    """
    git_root = git_root.resolve()

    rels: list[str] = []
    for ws in workspaces:
        ws_root = ws.workspace_root.resolve()
        if ws_root == git_root:
            rels.append("")
            continue
        try:
            rels.append(str(ws_root.relative_to(git_root)))
        except ValueError:
            rels.append("\x00")  # sentinel — never matches

    order = sorted(
        range(len(workspaces)),
        key=lambda i: len(rels[i]),
        reverse=True,
    )

    per_workspace: list[list[str]] = [[] for _ in workspaces]
    leftover: list[str] = []

    for filepath in changed_files:
        matched_idx: int | None = None
        rel_file: str = ""
        for i in order:
            rel = rels[i]
            if rel == "\x00":
                continue
            if rel == "":
                matched_idx = i
                rel_file = filepath
                break
            if filepath == rel:
                matched_idx = i
                rel_file = "."
                break
            prefix = rel + "/"
            if filepath.startswith(prefix):
                matched_idx = i
                rel_file = filepath[len(prefix) :]
                break
        if matched_idx is None:
            leftover.append(filepath)
        else:
            per_workspace[matched_idx].append(rel_file)

    return per_workspace, leftover


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
