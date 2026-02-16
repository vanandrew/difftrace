"""End-to-end integration tests using fixture lock content."""

from unittest.mock import patch

from difftrace.diff import map_files_to_packages, relativize_to_workspace
from difftrace.graph import parse_lock_file
from difftrace.traverse import find_affected_packages

from .conftest import DIAMOND_LOCK, SIMPLE_LOCK, VIRTUAL_ROOT_LOCK


class TestFullPipeline:
    def test_simple_leaf_change(self, tmp_path):
        """Change in api only affects api."""
        lock_file = tmp_path / "uv.lock"
        lock_file.write_text(SIMPLE_LOCK)
        graph = parse_lock_file(lock_file)

        changed_files = ["packages/api/src/handler.py"]
        directly_changed, test_all = map_files_to_packages(
            changed_files, graph.packages
        )
        affected = find_affected_packages(directly_changed, graph.reverse)

        assert directly_changed == {"api"}
        assert affected == {"api"}
        assert test_all is False

    def test_simple_shared_change(self, tmp_path):
        """Change in shared affects api and worker transitively."""
        lock_file = tmp_path / "uv.lock"
        lock_file.write_text(SIMPLE_LOCK)
        graph = parse_lock_file(lock_file)

        changed_files = ["packages/shared/models.py"]
        directly_changed, test_all = map_files_to_packages(
            changed_files, graph.packages
        )
        affected = find_affected_packages(directly_changed, graph.reverse)

        assert directly_changed == {"shared"}
        assert affected == {"shared", "api", "worker"}

    def test_diamond_transitive(self, tmp_path):
        """Change in shared propagates through diamond to app."""
        lock_file = tmp_path / "uv.lock"
        lock_file.write_text(DIAMOND_LOCK)
        graph = parse_lock_file(lock_file)

        changed_files = ["packages/shared/core.py"]
        directly_changed, test_all = map_files_to_packages(
            changed_files, graph.packages
        )
        affected = find_affected_packages(directly_changed, graph.reverse)

        assert directly_changed == {"shared"}
        assert affected == {"shared", "api", "worker", "app"}

    def test_root_trigger_tests_all(self, tmp_path):
        """Root pyproject.toml change triggers all packages."""
        lock_file = tmp_path / "uv.lock"
        lock_file.write_text(SIMPLE_LOCK)
        graph = parse_lock_file(lock_file)

        changed_files = ["pyproject.toml"]
        directly_changed, test_all = map_files_to_packages(
            changed_files, graph.packages
        )

        assert test_all is True
        # When test_all, all packages should be tested
        all_packages = set(graph.packages.keys())
        assert all_packages == {"api", "shared", "worker"}

    def test_no_changes(self, tmp_path):
        """No changed files means no affected packages."""
        lock_file = tmp_path / "uv.lock"
        lock_file.write_text(SIMPLE_LOCK)
        graph = parse_lock_file(lock_file)

        directly_changed, test_all = map_files_to_packages([], graph.packages)
        affected = find_affected_packages(directly_changed, graph.reverse)

        assert directly_changed == set()
        assert affected == set()
        assert test_all is False

    def test_virtual_root_not_matched(self, tmp_path):
        """Virtual root package should not match file changes."""
        lock_file = tmp_path / "uv.lock"
        lock_file.write_text(VIRTUAL_ROOT_LOCK)
        graph = parse_lock_file(lock_file)

        changed_files = ["packages/api/main.py"]
        directly_changed, test_all = map_files_to_packages(
            changed_files, graph.packages
        )
        affected = find_affected_packages(directly_changed, graph.reverse)

        assert directly_changed == {"api"}
        assert "myproject" in affected  # myproject depends on api

    def test_nested_workspace(self, tmp_path):
        """Workspace nested inside a git repo — paths are relativized correctly."""
        lock_file = tmp_path / "uv.lock"
        lock_file.write_text(SIMPLE_LOCK)
        graph = parse_lock_file(lock_file)

        git_root = tmp_path / "repo"
        git_root.mkdir()
        workspace_root = git_root / "python"
        workspace_root.mkdir()

        # Files as returned by git diff (relative to git root)
        git_files = [
            "python/packages/api/main.py",
            "python/packages/shared/lib.py",
            "docs/readme.md",
        ]
        workspace_files = relativize_to_workspace(git_files, git_root, workspace_root)
        assert workspace_files == ["packages/api/main.py", "packages/shared/lib.py"]

        directly_changed, test_all = map_files_to_packages(
            workspace_files, graph.packages
        )
        affected = find_affected_packages(directly_changed, graph.reverse)

        assert directly_changed == {"api", "shared"}
        assert affected == {"api", "shared", "worker"}
