import subprocess
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
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = "/home/user/repo\n"
        result = get_git_root()
        assert result == Path("/home/user/repo")
        mock_run.assert_called_once()

    @patch("difftrace.diff.subprocess.run")
    def test_not_a_git_repo(self, mock_run):
        mock_run.return_value.returncode = 128
        mock_run.return_value.stderr = "fatal: not a git repository"
        with pytest.raises(ValueError, match="Not a git repository"):
            get_git_root()

    @patch("difftrace.diff.subprocess.run")
    def test_git_root_timeout(self, mock_run):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="git", timeout=30)
        with pytest.raises(RuntimeError, match="timed out after 30 seconds"):
            get_git_root()


class TestGetChangedFiles:
    @patch("difftrace.diff.subprocess.run")
    def test_returns_file_list(self, mock_run):
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = (
            "packages/api/src/main.py\npackages/shared/lib.py\n"
        )
        result = get_changed_files("origin/main")
        assert result == ["packages/api/src/main.py", "packages/shared/lib.py"]

    @patch("difftrace.diff.subprocess.run")
    def test_empty_diff(self, mock_run):
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = ""
        result = get_changed_files("origin/main")
        assert result == []

    @patch("difftrace.diff.subprocess.run")
    def test_bad_ref(self, mock_run):
        mock_run.return_value.returncode = 128
        mock_run.return_value.stderr = "fatal: unknown revision 'nope'"
        with pytest.raises(ValueError, match="Could not resolve ref"):
            get_changed_files("nope")

    @patch("difftrace.diff.subprocess.run")
    def test_bad_ref_includes_fetch_depth_hint(self, mock_run):
        mock_run.return_value.returncode = 128
        mock_run.return_value.stderr = "fatal: unknown revision 'origin/main'"
        with pytest.raises(ValueError, match="fetch-depth: 0"):
            get_changed_files("origin/main")

    @patch("difftrace.diff.subprocess.run")
    def test_changed_files_timeout(self, mock_run):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="git", timeout=30)
        with pytest.raises(RuntimeError, match="timed out after 30 seconds"):
            get_changed_files("origin/main")

    def test_invalid_base_ref_empty(self):
        with pytest.raises(ValueError, match="must not be empty"):
            get_changed_files("")

    def test_invalid_base_ref_null_byte(self):
        with pytest.raises(ValueError, match="must not contain null bytes"):
            get_changed_files("origin/main\x00exploit")


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
        changed, test_all = map_files_to_packages(["packages/api/main.py"], packages)
        assert changed == {"api"}
        # Virtual root should not match
        assert "myproject" not in changed

    def test_no_match(self):
        packages = self._make_packages()
        changed, test_all = map_files_to_packages(["some/random/file.txt"], packages)
        assert changed == set()
        assert test_all is False

    def test_prefix_no_false_match(self):
        """'packages/api-extra/foo.py' should NOT match 'packages/api'."""
        packages = self._make_packages()
        changed, test_all = map_files_to_packages(
            ["packages/api-extra/foo.py"], packages
        )
        assert changed == set()

    def test_custom_root_trigger(self):
        packages = self._make_packages()
        changed, test_all = map_files_to_packages(
            ["Dockerfile"],
            packages,
            root_triggers={"Dockerfile"},
            dir_triggers=set(),
        )
        assert test_all is True

    def test_custom_dir_trigger(self):
        packages = self._make_packages()
        changed, test_all = map_files_to_packages(
            ["docker/compose.yml"],
            packages,
            root_triggers=set(),
            dir_triggers={"docker/"},
        )
        assert test_all is True

    def test_custom_triggers_override_defaults(self):
        """When custom triggers are passed, defaults are not used."""
        packages = self._make_packages()
        _, test_all = map_files_to_packages(
            ["pyproject.toml"],
            packages,
            root_triggers=set(),
            dir_triggers=set(),
        )
        assert test_all is False

    def test_glob_root_trigger(self):
        """Glob pattern 'Dockerfile.*' matches 'Dockerfile.prod'."""
        packages = self._make_packages()
        _, test_all = map_files_to_packages(
            ["Dockerfile.prod"],
            packages,
            root_triggers={"Dockerfile.*"},
            dir_triggers=set(),
        )
        assert test_all is True

    def test_glob_no_false_match(self):
        """Glob pattern 'Dockerfile.*' should not match 'README.md'."""
        packages = self._make_packages()
        _, test_all = map_files_to_packages(
            ["README.md"],
            packages,
            root_triggers={"Dockerfile.*"},
            dir_triggers=set(),
        )
        assert test_all is False

    def test_glob_question_mark(self):
        """Glob pattern 'config.?' matches 'config.a'."""
        packages = self._make_packages()
        _, test_all = map_files_to_packages(
            ["config.a"],
            packages,
            root_triggers={"config.?"},
            dir_triggers=set(),
        )
        assert test_all is True

    def test_unicode_file_path(self):
        """Non-ASCII file paths are matched correctly."""
        packages = {
            "api": WorkspacePackage(name="api", source_path="packages/api"),
        }
        changed, test_all = map_files_to_packages(
            ["packages/api/données/fichier.py"],
            packages,
        )
        assert changed == {"api"}
        assert test_all is False
