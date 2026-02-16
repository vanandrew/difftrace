from __future__ import annotations

from collections import deque


def find_affected_packages(
    directly_changed: set[str],
    reverse_deps: dict[str, set[str]],
) -> set[str]:
    """BFS over reverse dependencies to find all transitively affected packages.

    Args:
        directly_changed: Set of package names that were directly changed.
        reverse_deps: Mapping of package name → set of packages that depend on it.

    Returns:
        All transitively affected packages, including the directly changed ones.
    """
    affected: set[str] = set()
    queue: deque[str] = deque()

    for pkg in directly_changed:
        if pkg not in affected:
            affected.add(pkg)
            queue.append(pkg)

    while queue:
        current = queue.popleft()
        for dependent in reverse_deps.get(current, set()):
            if dependent not in affected:
                affected.add(dependent)
                queue.append(dependent)

    return affected
