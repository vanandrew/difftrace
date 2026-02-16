from pathlib import Path
from unittest.mock import patch

import pytest

from difftrace.diff import (
    get_changed_files,
    get_git_root,
    map_files_to_packages,
    relativize_to_workspace,
)
from difftrace.graph import WorkspacePackage


class TestGetGitRoot:
    @patch("difftrace.diff.subprocess.run")
    def test_returns_path(self, mock_run):
        mock_run.return_value.stdout = "/home/user/repo\n"
        result = get_git_root()
        assert result == Path("/home/user/repo")
        mock_run.assert_called_once()


class TestGetChangedFiles:
    @patch("difftrace.diff.subprocess.run")
    def test_returns_file_list(self, mock_run):
        mock_run.return_value.stdout = "packages/api/src/main.py\npackages/shared/lib.py\n"
        result = get_changed_files("origin/main")
        assert result == ["packages/api/src/main.py", "packages/shared/lib.py"]

    @patch("difftrace.diff.subprocess.run")
    def test_empty_diff(self, mock_run):
        mock_run.return_value.stdout = ""
        result = get_changed_files("origin/main")
        assert result == []


class TestRelativizeToWorkspace:
    def test_same_root(self, tmp_path):
        files = ["packages/api/main.py", "uv.lock"]
        result = relativize_to_workspace(files, tmp_path, tmp_path)
        assert result == files

    def test_nested_workspace(self, tmp_path):
        git_root = tmp_path
        workspace_root = tmp_path / "python"
        workspace_root.mkdir()

        files = [
            "python/packages/api/main.py",
            "python/uv.lock",
            "other/file.txt",
        ]
        result = relativize_to_workspace(files, git_root, workspace_root)
        assert result == ["packages/api/main.py", "uv.lock"]

    def test_outside_workspace_dropped(self, tmp_path):
        git_root = tmp_path
        workspace_root = tmp_path / "python"
        workspace_root.mkdir()

        files = ["README.md", "other/file.txt"]
        result = relativize_to_workspace(files, git_root, workspace_root)
        assert result == []


class TestMapFilesToPackages:
    def _make_packages(self):
        return {
            "api": WorkspacePackage(name="api", source_path="packages/api"),
            "shared": WorkspacePackage(name="shared", source_path="packages/shared"),
            "worker": WorkspacePackage(name="worker", source_path="packages/worker"),
        }

    def test_basic_mapping(self):
        packages = self._make_packages()
        changed, test_all = map_files_to_packages(
            ["packages/api/src/main.py"], packages
        )
        assert changed == {"api"}
        assert test_all is False

    def test_multiple_packages(self):
        packages = self._make_packages()
        changed, test_all = map_files_to_packages(
            ["packages/api/src/main.py", "packages/shared/lib.py"], packages
        )
        assert changed == {"api", "shared"}
        assert test_all is False

    def test_root_pyproject_triggers_test_all(self):
        packages = self._make_packages()
        changed, test_all = map_files_to_packages(["pyproject.toml"], packages)
        assert test_all is True

    def test_uv_lock_triggers_test_all(self):
        packages = self._make_packages()
        changed, test_all = map_files_to_packages(["uv.lock"], packages)
        assert test_all is True

    def test_github_dir_triggers_test_all(self):
        packages = self._make_packages()
        changed, test_all = map_files_to_packages(
            [".github/workflows/ci.yml"], packages
        )
        assert test_all is True

    def test_sub_package_pyproject_no_trigger(self):
        packages = self._make_packages()
        changed, test_all = map_files_to_packages(
            ["packages/api/pyproject.toml"], packages
        )
        assert changed == {"api"}
        assert test_all is False

    def test_virtual_root_skipped(self):
        packages = {
            "myproject": WorkspacePackage(name="myproject", source_path="."),
            "api": WorkspacePackage(name="api", source_path="packages/api"),
        }
        changed, test_all = map_files_to_packages(
            ["packages/api/main.py"], packages
        )
        assert changed == {"api"}
        # Virtual root should not match
        assert "myproject" not in changed

    def test_no_match(self):
        packages = self._make_packages()
        changed, test_all = map_files_to_packages(
            ["some/random/file.txt"], packages
        )
        assert changed == set()
        assert test_all is False

    def test_prefix_no_false_match(self):
        """'packages/api-extra/foo.py' should NOT match 'packages/api'."""
        packages = self._make_packages()
        changed, test_all = map_files_to_packages(
            ["packages/api-extra/foo.py"], packages
        )
        assert changed == set()
