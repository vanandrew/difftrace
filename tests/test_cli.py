from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from difftrace.cli import (
    _normalize_lock_arg,
    _parse_triggers,
    _print_human,
    _source_display_path,
    _workspace_label,
    build_parser,
    main,
    run,
)
from difftrace.diff import DEFAULT_DIR_TRIGGERS, DEFAULT_ROOT_TRIGGERS


class TestBuildParser:
    def test_default_base(self):
        parser = build_parser()
        args = parser.parse_args([])
        assert args.base == "origin/main"

    def test_custom_base(self):
        parser = build_parser()
        args = parser.parse_args(["--base", "develop"])
        assert args.base == "develop"

    def test_mutually_exclusive_output(self):
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["--json", "--names"])

    def test_default_lock_file(self):
        parser = build_parser()
        args = parser.parse_args([])
        assert args.lock_file is None

    def test_custom_lock_file(self):
        parser = build_parser()
        args = parser.parse_args(["--lock-file", "custom.lock"])
        assert args.lock_file == ["custom.lock"]

    def test_multiple_lock_files(self):
        parser = build_parser()
        args = parser.parse_args(
            ["--lock-file", "python/uv.lock", "--lock-file", "python2/uv.lock"]
        )
        assert args.lock_file == ["python/uv.lock", "python2/uv.lock"]

    def test_root_trigger_append(self):
        parser = build_parser()
        args = parser.parse_args(
            ["--root-trigger", "Dockerfile", "--root-trigger", "Makefile"]
        )
        assert args.root_trigger == ["Dockerfile", "Makefile"]

    def test_exclude_append(self):
        parser = build_parser()
        args = parser.parse_args(["--exclude", "api", "--exclude", "worker"])
        assert args.exclude == ["api", "worker"]

    def test_verbose_flag(self):
        parser = build_parser()
        args = parser.parse_args(["--verbose"])
        assert args.verbose is True

    def test_verbose_short_flag(self):
        parser = build_parser()
        args = parser.parse_args(["-v"])
        assert args.verbose is True

    def test_no_dev_flag(self):
        parser = build_parser()
        args = parser.parse_args(["--no-dev"])
        assert args.no_dev is True

    def test_no_optional_flag(self):
        parser = build_parser()
        args = parser.parse_args(["--no-optional"])
        assert args.no_optional is True

    def test_direct_only_flag(self):
        parser = build_parser()
        args = parser.parse_args(["--direct-only"])
        assert args.direct_only is True

    def test_detailed_flag(self):
        parser = build_parser()
        args = parser.parse_args(["--detailed"])
        assert args.detailed is True

    def test_test_all_flag(self):
        parser = build_parser()
        args = parser.parse_args(["--test-all"])
        assert args.test_all is True

    def test_defaults(self):
        parser = build_parser()
        args = parser.parse_args([])
        assert args.json_output is False
        assert args.names is False
        assert args.paths is False
        assert args.no_dev is False
        assert args.no_optional is False
        assert args.direct_only is False
        assert args.test_all is False
        assert args.detailed is False
        assert args.verbose is False
        assert args.root_trigger is None
        assert args.exclude is None


class TestParseTriggers:
    def test_no_extra(self):
        root, dirs = _parse_triggers(None)
        assert root == DEFAULT_ROOT_TRIGGERS
        assert dirs == DEFAULT_DIR_TRIGGERS

    def test_file_trigger(self):
        root, _dirs = _parse_triggers(["Dockerfile"])
        assert "Dockerfile" in root
        assert root >= DEFAULT_ROOT_TRIGGERS

    def test_dir_trigger(self):
        _root, dirs = _parse_triggers(["docker/"])
        assert "docker/" in dirs
        assert dirs >= DEFAULT_DIR_TRIGGERS

    def test_mixed_triggers(self):
        root, dirs = _parse_triggers(["Dockerfile", "docker/"])
        assert "Dockerfile" in root
        assert "docker/" in dirs


def _sha_result(sha: str):
    """Create a mock subprocess result for _resolve_sha calls."""
    return type("R", (), {"returncode": 0, "stdout": sha + "\n", "stderr": ""})()


SIMPLE_LOCK = """\
version = 1

[manifest]
members = ["api", "shared", "worker"]

[[package]]
name = "api"
version = "0.1.0"
source = { editable = "packages/api" }
dependencies = [
    { name = "shared" },
]

[[package]]
name = "shared"
version = "0.1.0"
source = { editable = "packages/shared" }
dependencies = []

[[package]]
name = "worker"
version = "0.1.0"
source = { editable = "packages/worker" }
dependencies = [
    { name = "shared" },
]
"""


class TestRun:
    def _make_args(self, tmp_path, **overrides):
        lock_file = tmp_path / "uv.lock"
        lock_file.write_text(SIMPLE_LOCK)

        parser = build_parser()
        defaults = parser.parse_args([])
        defaults.lock_file = str(lock_file)

        for k, v in overrides.items():
            setattr(defaults, k, v)
        return defaults

    @patch("difftrace.diff.subprocess.run")
    def test_basic_run(self, mock_run, tmp_path):
        # First call: get_git_root
        git_root_result = type(
            "R",
            (),
            {
                "returncode": 0,
                "stdout": str(tmp_path) + "\n",
                "stderr": "",
            },
        )()
        # Second call: get_changed_files
        diff_result = type(
            "R",
            (),
            {
                "returncode": 0,
                "stdout": "packages/api/main.py\n",
                "stderr": "",
            },
        )()
        mock_run.side_effect = [
            git_root_result,
            _sha_result("aaa"),
            _sha_result("bbb"),
            diff_result,
        ]

        args = self._make_args(tmp_path)
        result = run(args)
        assert "api" in result["affected"]
        assert result["test_all"] is False

    @patch("difftrace.diff.subprocess.run")
    def test_direct_only(self, mock_run, tmp_path):
        git_root_result = type(
            "R",
            (),
            {
                "returncode": 0,
                "stdout": str(tmp_path) + "\n",
                "stderr": "",
            },
        )()
        diff_result = type(
            "R",
            (),
            {
                "returncode": 0,
                "stdout": "packages/shared/lib.py\n",
                "stderr": "",
            },
        )()
        mock_run.side_effect = [
            git_root_result,
            _sha_result("aaa"),
            _sha_result("bbb"),
            diff_result,
        ]

        args = self._make_args(tmp_path, direct_only=True)
        result = run(args)
        # shared is directly changed; api/worker are transitive but excluded
        assert result["directly_changed"] == ["shared"]
        assert result["affected"] == ["shared"]

    @patch("difftrace.diff.subprocess.run")
    def test_test_all(self, mock_run, tmp_path):
        git_root_result = type(
            "R",
            (),
            {
                "returncode": 0,
                "stdout": str(tmp_path) + "\n",
                "stderr": "",
            },
        )()
        diff_result = type(
            "R",
            (),
            {
                "returncode": 0,
                "stdout": "pyproject.toml\n",
                "stderr": "",
            },
        )()
        mock_run.side_effect = [
            git_root_result,
            _sha_result("aaa"),
            _sha_result("bbb"),
            diff_result,
        ]

        args = self._make_args(tmp_path)
        result = run(args)
        assert result["test_all"] is True
        assert set(result["affected"]) == {"api", "shared", "worker"}

    @patch("difftrace.diff.subprocess.run")
    def test_detailed_output(self, mock_run, tmp_path):
        git_root_result = type(
            "R",
            (),
            {
                "returncode": 0,
                "stdout": str(tmp_path) + "\n",
                "stderr": "",
            },
        )()
        diff_result = type(
            "R",
            (),
            {
                "returncode": 0,
                "stdout": "packages/api/main.py\n",
                "stderr": "",
            },
        )()
        mock_run.side_effect = [
            git_root_result,
            _sha_result("aaa"),
            _sha_result("bbb"),
            diff_result,
        ]

        args = self._make_args(tmp_path, detailed=True)
        result = run(args)
        assert "file_mapping" in result
        assert result["file_mapping"]["packages/api/main.py"] == "api"

    @patch("difftrace.diff.subprocess.run")
    def test_exclude(self, mock_run, tmp_path):
        git_root_result = type(
            "R",
            (),
            {
                "returncode": 0,
                "stdout": str(tmp_path) + "\n",
                "stderr": "",
            },
        )()
        diff_result = type(
            "R",
            (),
            {
                "returncode": 0,
                "stdout": "packages/shared/lib.py\n",
                "stderr": "",
            },
        )()
        mock_run.side_effect = [
            git_root_result,
            _sha_result("aaa"),
            _sha_result("bbb"),
            diff_result,
        ]

        args = self._make_args(tmp_path, exclude=["api"])
        result = run(args)
        # shared changed → api, worker are transitive. api is excluded.
        assert "api" not in result["affected"]
        assert "shared" in result["affected"]
        assert "worker" in result["affected"]

    def test_test_all_flag(self, tmp_path):
        """--test-all skips git diff and returns all packages."""
        args = self._make_args(tmp_path, test_all=True)
        result = run(args)
        assert result["test_all"] is True
        assert set(result["affected"]) == {"api", "shared", "worker"}
        assert result["directly_changed"] == []
        assert result["changed_files"] == []

    def test_test_all_with_exclude(self, tmp_path):
        """--test-all respects --exclude."""
        args = self._make_args(tmp_path, test_all=True, exclude=["api"])
        result = run(args)
        assert result["test_all"] is True
        assert "api" not in result["affected"]
        assert set(result["affected"]) == {"shared", "worker"}

    def test_test_all_excludes_virtual_roots(self, tmp_path):
        """--test-all excludes virtual root packages."""
        from tests.conftest import VIRTUAL_ROOT_LOCK

        lock_file = tmp_path / "uv.lock"
        lock_file.write_text(VIRTUAL_ROOT_LOCK)
        parser = build_parser()
        defaults = parser.parse_args([])
        defaults.lock_file = str(lock_file)
        defaults.test_all = True
        result = run(defaults)
        assert "myproject" not in result["affected"]
        assert set(result["affected"]) == {"api", "lib"}

    @patch("difftrace.diff.subprocess.run")
    def test_no_dev(self, mock_run, tmp_path):
        git_root_result = type(
            "R",
            (),
            {
                "returncode": 0,
                "stdout": str(tmp_path) + "\n",
                "stderr": "",
            },
        )()
        diff_result = type(
            "R",
            (),
            {
                "returncode": 0,
                "stdout": "packages/api/main.py\n",
                "stderr": "",
            },
        )()
        mock_run.side_effect = [
            git_root_result,
            _sha_result("aaa"),
            _sha_result("bbb"),
            diff_result,
        ]

        args = self._make_args(tmp_path, no_dev=True)
        result = run(args)
        assert "api" in result["affected"]

    @patch("difftrace.diff.subprocess.run")
    def test_no_optional(self, mock_run, tmp_path):
        git_root_result = type(
            "R",
            (),
            {
                "returncode": 0,
                "stdout": str(tmp_path) + "\n",
                "stderr": "",
            },
        )()
        diff_result = type(
            "R",
            (),
            {
                "returncode": 0,
                "stdout": "packages/api/main.py\n",
                "stderr": "",
            },
        )()
        mock_run.side_effect = [
            git_root_result,
            _sha_result("aaa"),
            _sha_result("bbb"),
            diff_result,
        ]

        args = self._make_args(tmp_path, no_optional=True)
        result = run(args)
        assert "api" in result["affected"]


class TestMain:
    @patch("difftrace.cli.run")
    def test_json_output(self, mock_run, capsys):
        mock_run.return_value = {
            "directly_changed": ["api"],
            "affected": ["api", "shared"],
            "test_all": False,
            "packages": {},
            "changed_files": [],
            "file_mapping": {},
        }
        with patch("sys.argv", ["difftrace", "--json"]):
            main()
        out = capsys.readouterr().out
        data = json.loads(out)
        assert data["affected"] == ["api", "shared"]
        assert "packages" not in data
        assert "file_mapping" not in data

    @patch("difftrace.cli.run")
    def test_names_output(self, mock_run, capsys):
        mock_run.return_value = {
            "directly_changed": ["api"],
            "affected": ["api", "shared"],
            "test_all": False,
            "packages": {},
            "changed_files": [],
            "file_mapping": {},
        }
        with patch("sys.argv", ["difftrace", "--names"]):
            main()
        out = capsys.readouterr().out
        assert out.strip().splitlines() == ["api", "shared"]

    @patch("difftrace.cli.run")
    def test_paths_output(self, mock_run, capsys):
        from difftrace.graph import WorkspacePackage

        mock_run.return_value = {
            "directly_changed": ["api"],
            "affected": ["api"],
            "test_all": False,
            "packages": {
                "api": WorkspacePackage(name="api", source_path="packages/api"),
            },
            "changed_files": [],
            "file_mapping": {},
        }
        with patch("sys.argv", ["difftrace", "--paths"]):
            main()
        out = capsys.readouterr().out
        assert out.strip() == "packages/api"

    @patch("difftrace.cli.run")
    def test_human_output(self, mock_run, capsys):
        mock_run.return_value = {
            "directly_changed": ["api"],
            "affected": ["api", "shared"],
            "test_all": False,
            "packages": {},
            "changed_files": [],
            "file_mapping": {},
        }
        with patch("sys.argv", ["difftrace"]):
            main()
        out = capsys.readouterr().out
        assert "Affected packages (2)" in out

    @patch("difftrace.cli.run")
    def test_error_file_not_found(self, mock_run, capsys):
        mock_run.side_effect = FileNotFoundError("uv.lock not found")
        with patch("sys.argv", ["difftrace"]), pytest.raises(SystemExit, match="1"):
            main()
        err = capsys.readouterr().err
        assert "uv.lock not found" in err

    @patch("difftrace.cli.run")
    def test_error_timeout(self, mock_run, capsys):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="git", timeout=30)
        with patch("sys.argv", ["difftrace"]), pytest.raises(SystemExit, match="1"):
            main()

    @patch("difftrace.cli.run")
    def test_error_called_process_error(self, mock_run, capsys):
        mock_run.side_effect = subprocess.CalledProcessError(returncode=1, cmd="git")
        with patch("sys.argv", ["difftrace"]), pytest.raises(SystemExit, match="1"):
            main()


class TestExcludeCli:
    @patch("difftrace.diff.subprocess.run")
    def test_exclude_single_package(self, mock_run, tmp_path):
        lock_file = tmp_path / "uv.lock"
        lock_file.write_text(SIMPLE_LOCK)

        git_root_result = type(
            "R",
            (),
            {
                "returncode": 0,
                "stdout": str(tmp_path) + "\n",
                "stderr": "",
            },
        )()
        diff_result = type(
            "R",
            (),
            {
                "returncode": 0,
                "stdout": "packages/shared/lib.py\n",
                "stderr": "",
            },
        )()
        mock_run.side_effect = [
            git_root_result,
            _sha_result("aaa"),
            _sha_result("bbb"),
            diff_result,
        ]

        parser = build_parser()
        args = parser.parse_args(
            [
                "--lock-file",
                str(lock_file),
                "--exclude",
                "api",
            ]
        )
        result = run(args)
        assert "api" not in result["affected"]

    @patch("difftrace.diff.subprocess.run")
    def test_exclude_multiple(self, mock_run, tmp_path):
        lock_file = tmp_path / "uv.lock"
        lock_file.write_text(SIMPLE_LOCK)

        git_root_result = type(
            "R",
            (),
            {
                "returncode": 0,
                "stdout": str(tmp_path) + "\n",
                "stderr": "",
            },
        )()
        diff_result = type(
            "R",
            (),
            {
                "returncode": 0,
                "stdout": "packages/shared/lib.py\n",
                "stderr": "",
            },
        )()
        mock_run.side_effect = [
            git_root_result,
            _sha_result("aaa"),
            _sha_result("bbb"),
            diff_result,
        ]

        parser = build_parser()
        args = parser.parse_args(
            [
                "--lock-file",
                str(lock_file),
                "--exclude",
                "api",
                "--exclude",
                "worker",
            ]
        )
        result = run(args)
        assert "api" not in result["affected"]
        assert "worker" not in result["affected"]
        assert "shared" in result["affected"]

    @patch("difftrace.diff.subprocess.run")
    def test_exclude_nonexistent(self, mock_run, tmp_path):
        """Excluding a package that isn't affected doesn't cause an error."""
        lock_file = tmp_path / "uv.lock"
        lock_file.write_text(SIMPLE_LOCK)

        git_root_result = type(
            "R",
            (),
            {
                "returncode": 0,
                "stdout": str(tmp_path) + "\n",
                "stderr": "",
            },
        )()
        diff_result = type(
            "R",
            (),
            {
                "returncode": 0,
                "stdout": "packages/api/main.py\n",
                "stderr": "",
            },
        )()
        mock_run.side_effect = [
            git_root_result,
            _sha_result("aaa"),
            _sha_result("bbb"),
            diff_result,
        ]

        parser = build_parser()
        args = parser.parse_args(
            [
                "--lock-file",
                str(lock_file),
                "--exclude",
                "nonexistent",
            ]
        )
        result = run(args)
        assert "api" in result["affected"]


class TestSingleLockNestedTrigger:
    """Single-lock in a nested directory: workspace-relative triggers still
    set global test_all (legacy behavior required by GitHub Action tests)."""

    @patch("difftrace.diff.subprocess.run")
    def test_nested_workspace_pyproject_triggers_test_all(self, mock_run, tmp_path):
        """A change to <workspace>/pyproject.toml sets test_all=true in single-lock mode."""
        git_root = tmp_path
        ws_root = tmp_path / "nested"
        ws_root.mkdir()
        lock_file = ws_root / "uv.lock"
        lock_file.write_text(SIMPLE_LOCK)

        git_root_result = type(
            "R",
            (),
            {"returncode": 0, "stdout": str(git_root) + "\n", "stderr": ""},
        )()
        diff_result = type(
            "R",
            (),
            {
                "returncode": 0,
                "stdout": "nested/pyproject.toml\n",
                "stderr": "",
            },
        )()
        mock_run.side_effect = [
            git_root_result,
            _sha_result("aaa"),
            _sha_result("bbb"),
            diff_result,
        ]

        parser = build_parser()
        args = parser.parse_args(["--lock-file", str(lock_file)])
        result = run(args)
        assert result["test_all"] is True
        assert set(result["affected"]) == {"api", "shared", "worker"}


class TestMultiLockRun:
    """Multi-lock orchestration: two sibling sub-workspaces."""

    def _git_root_result(self, path):
        return type(
            "R",
            (),
            {"returncode": 0, "stdout": str(path) + "\n", "stderr": ""},
        )()

    def _diff_result(self, files: str):
        return type(
            "R",
            (),
            {"returncode": 0, "stdout": files, "stderr": ""},
        )()

    @patch("difftrace.diff.subprocess.run")
    def test_multi_lock_routes_files(self, mock_run, two_workspace_tree):
        """A change in python/ only affects the python workspace."""
        tree = two_workspace_tree
        mock_run.side_effect = [
            self._git_root_result(tree["root"]),
            _sha_result("aaa"),
            _sha_result("bbb"),
            self._diff_result("python/packages/shared/lib.py\n"),
        ]
        parser = build_parser()
        args = parser.parse_args(
            [
                "--lock-file",
                str(tree["py_lock"]),
                "--lock-file",
                str(tree["py2_lock"]),
            ]
        )
        result = run(args)
        assert result["_is_multi"] is True
        assert result["test_all"] is False
        # directly_changed contains only python/shared (not python2/*)
        assert result["directly_changed"] == [{"name": "shared", "workspace": "python"}]
        # affected includes python/shared and python/api (transitive)
        assert result["affected"] == [
            {"name": "api", "workspace": "python"},
            {"name": "shared", "workspace": "python"},
        ]

    @patch("difftrace.diff.subprocess.run")
    def test_multi_lock_name_collision(self, mock_run, two_workspace_tree):
        """'api' exists in both workspaces; qualified output disambiguates."""
        tree = two_workspace_tree
        mock_run.side_effect = [
            self._git_root_result(tree["root"]),
            _sha_result("aaa"),
            _sha_result("bbb"),
            self._diff_result("python/packages/api/x.py\npython2/packages/api/y.py\n"),
        ]
        parser = build_parser()
        args = parser.parse_args(
            [
                "--lock-file",
                str(tree["py_lock"]),
                "--lock-file",
                str(tree["py2_lock"]),
            ]
        )
        result = run(args)
        assert result["directly_changed"] == [
            {"name": "api", "workspace": "python"},
            {"name": "api", "workspace": "python2"},
        ]

    @patch("difftrace.diff.subprocess.run")
    def test_multi_lock_global_test_all_from_root_file(
        self, mock_run, two_workspace_tree
    ):
        """A change to a git-root-level pyproject.toml sets global test_all."""
        tree = two_workspace_tree
        mock_run.side_effect = [
            self._git_root_result(tree["root"]),
            _sha_result("aaa"),
            _sha_result("bbb"),
            self._diff_result("pyproject.toml\n"),
        ]
        parser = build_parser()
        args = parser.parse_args(
            [
                "--lock-file",
                str(tree["py_lock"]),
                "--lock-file",
                str(tree["py2_lock"]),
            ]
        )
        result = run(args)
        assert result["test_all"] is True
        # Every package across both workspaces appears
        names = {(e["workspace"], e["name"]) for e in result["affected"]}
        assert names == {
            ("python", "api"),
            ("python", "shared"),
            ("python2", "api"),
            ("python2", "worker"),
        }

    @patch("difftrace.diff.subprocess.run")
    def test_sub_workspace_lock_change_scopes_to_workspace(
        self, mock_run, two_workspace_tree
    ):
        """A change to python/uv.lock marks all python members directly_changed
        but leaves global test_all=False and python2 untouched."""
        tree = two_workspace_tree
        mock_run.side_effect = [
            self._git_root_result(tree["root"]),
            _sha_result("aaa"),
            _sha_result("bbb"),
            self._diff_result("python/uv.lock\n"),
        ]
        parser = build_parser()
        args = parser.parse_args(
            [
                "--lock-file",
                str(tree["py_lock"]),
                "--lock-file",
                str(tree["py2_lock"]),
            ]
        )
        result = run(args)
        assert result["test_all"] is False
        directly = {(e["workspace"], e["name"]) for e in result["directly_changed"]}
        assert directly == {("python", "api"), ("python", "shared")}
        # python2 packages untouched
        affected = {(e["workspace"], e["name"]) for e in result["affected"]}
        assert ("python2", "api") not in affected
        assert ("python2", "worker") not in affected

    @patch("difftrace.diff.subprocess.run")
    def test_multi_lock_test_all_flag(self, mock_run, two_workspace_tree):
        """--test-all skips git diff and returns all packages across all workspaces."""
        tree = two_workspace_tree
        # test_all=True still needs git_root for multi-lock labels.
        mock_run.side_effect = [self._git_root_result(tree["root"])]
        parser = build_parser()
        args = parser.parse_args(
            [
                "--lock-file",
                str(tree["py_lock"]),
                "--lock-file",
                str(tree["py2_lock"]),
                "--test-all",
            ]
        )
        result = run(args)
        assert result["test_all"] is True
        names = {(e["workspace"], e["name"]) for e in result["affected"]}
        assert names == {
            ("python", "api"),
            ("python", "shared"),
            ("python2", "api"),
            ("python2", "worker"),
        }


class TestMultiLockMain:
    """Multi-lock output formatting in main()."""

    @patch("difftrace.cli.run")
    def test_json_output_multi(self, mock_run, capsys):
        mock_run.return_value = {
            "directly_changed": [{"name": "api", "workspace": "python"}],
            "affected": [
                {"name": "api", "workspace": "python"},
                {"name": "shared", "workspace": "python"},
            ],
            "test_all": False,
            "workspaces": ["python", "python2"],
            "changed_files": [],
            "file_mapping": {},
            "_workspaces": [],
            "_ws_labels": ["python", "python2"],
            "_is_multi": True,
        }
        with patch("sys.argv", ["difftrace", "--json"]):
            main()
        data = json.loads(capsys.readouterr().out)
        assert data["affected"] == [
            {"name": "api", "workspace": "python"},
            {"name": "shared", "workspace": "python"},
        ]
        assert data["workspaces"] == ["python", "python2"]
        assert "_is_multi" not in data

    @patch("difftrace.cli.run")
    def test_names_output_multi(self, mock_run, capsys):
        mock_run.return_value = {
            "directly_changed": [],
            "affected": [
                {"name": "api", "workspace": "python"},
                {"name": "api", "workspace": "python2"},
            ],
            "test_all": False,
            "workspaces": ["python", "python2"],
            "changed_files": [],
            "file_mapping": {},
            "_workspaces": [],
            "_ws_labels": ["python", "python2"],
            "_is_multi": True,
        }
        with patch("sys.argv", ["difftrace", "--names"]):
            main()
        out = capsys.readouterr().out
        assert out.strip().splitlines() == ["python/api", "python2/api"]

    @patch("difftrace.cli.run")
    def test_paths_output_multi(self, mock_run, capsys):
        from difftrace.graph import DependencyGraph, Workspace, WorkspacePackage

        py_ws = Workspace(
            lock_path=Path("/r/python/uv.lock"),
            workspace_root=Path("/r/python"),
            graph=DependencyGraph(
                packages={
                    "api": WorkspacePackage(name="api", source_path="packages/api"),
                }
            ),
        )
        py2_ws = Workspace(
            lock_path=Path("/r/python2/uv.lock"),
            workspace_root=Path("/r/python2"),
            graph=DependencyGraph(
                packages={
                    "api": WorkspacePackage(name="api", source_path="packages/api"),
                }
            ),
        )
        mock_run.return_value = {
            "directly_changed": [],
            "affected": [
                {"name": "api", "workspace": "python"},
                {"name": "api", "workspace": "python2"},
            ],
            "test_all": False,
            "workspaces": ["python", "python2"],
            "changed_files": [],
            "file_mapping": {},
            "_workspaces": [py_ws, py2_ws],
            "_ws_labels": ["python", "python2"],
            "_is_multi": True,
        }
        with patch("sys.argv", ["difftrace", "--paths"]):
            main()
        out = capsys.readouterr().out
        assert out.strip().splitlines() == [
            "python/packages/api",
            "python2/packages/api",
        ]

    @patch("difftrace.cli.run")
    def test_json_detailed_includes_file_mapping(self, mock_run, capsys):
        """--json --detailed emits file_mapping alongside the normal output."""
        mock_run.return_value = {
            "directly_changed": ["api"],
            "affected": ["api"],
            "test_all": False,
            "packages": {},
            "changed_files": ["packages/api/main.py"],
            "file_mapping": {"packages/api/main.py": "api"},
            "_workspaces": [],
            "_ws_labels": [""],
            "_is_multi": False,
        }
        with patch("sys.argv", ["difftrace", "--json", "--detailed"]):
            main()
        data = json.loads(capsys.readouterr().out)
        assert data["file_mapping"] == {"packages/api/main.py": "api"}

    @patch("difftrace.diff.subprocess.run")
    def test_multi_lock_detailed_file_mapping(self, mock_run, two_workspace_tree):
        """--detailed in multi-lock uses git-root-relative keys and qualified values."""
        tree = two_workspace_tree
        mock_run.side_effect = [
            type(
                "R",
                (),
                {"returncode": 0, "stdout": str(tree["root"]) + "\n", "stderr": ""},
            )(),
            _sha_result("aaa"),
            _sha_result("bbb"),
            type(
                "R",
                (),
                {
                    "returncode": 0,
                    "stdout": (
                        "python/packages/api/main.py\n"
                        "python2/packages/worker/lib.py\n"
                        "README.md\n"
                    ),
                    "stderr": "",
                },
            )(),
        ]
        parser = build_parser()
        args = parser.parse_args(
            [
                "--lock-file",
                str(tree["py_lock"]),
                "--lock-file",
                str(tree["py2_lock"]),
                "--detailed",
            ]
        )
        result = run(args)
        fm = result["file_mapping"]
        assert fm["python/packages/api/main.py"] == "python/api"
        assert fm["python2/packages/worker/lib.py"] == "python2/worker"
        # Root-level file that matched no workspace is reported as unmapped
        assert fm["README.md"] is None

    @patch("difftrace.cli.run")
    def test_human_output_multi(self, mock_run, capsys):
        mock_run.return_value = {
            "directly_changed": [{"name": "shared", "workspace": "python"}],
            "affected": [
                {"name": "api", "workspace": "python"},
                {"name": "shared", "workspace": "python"},
                {"name": "worker", "workspace": "python2"},
            ],
            "test_all": False,
            "workspaces": ["python", "python2"],
            "changed_files": [],
            "file_mapping": {},
            "_workspaces": [],
            "_ws_labels": ["python", "python2"],
            "_is_multi": True,
        }
        with patch("sys.argv", ["difftrace"]):
            main()
        out = capsys.readouterr().out
        assert "Affected packages (3)" in out
        assert "python/shared (direct)" in out
        assert "python/api (transitive)" in out
        assert "python2/worker (transitive)" in out


class TestHelpers:
    """Small helper functions exposed for multi-lock bookkeeping."""

    def test_normalize_lock_arg_default(self):
        assert _normalize_lock_arg(None) == [Path("uv.lock")]
        assert _normalize_lock_arg([]) == [Path("uv.lock")]

    def test_normalize_lock_arg_string(self):
        assert _normalize_lock_arg("foo.lock") == [Path("foo.lock")]

    def test_normalize_lock_arg_list(self):
        assert _normalize_lock_arg(["a.lock", "b.lock"]) == [
            Path("a.lock"),
            Path("b.lock"),
        ]

    def test_workspace_label_no_git_root(self, tmp_path):
        from difftrace.graph import DependencyGraph, Workspace

        ws = Workspace(
            lock_path=tmp_path / "uv.lock",
            workspace_root=tmp_path,
            graph=DependencyGraph(),
        )
        assert _workspace_label(ws, None) == ""

    def test_workspace_label_outside_git_root(self, tmp_path):
        """Workspace root not under git root falls back to the absolute path."""
        from difftrace.graph import DependencyGraph, Workspace

        outside = tmp_path / "outside"
        outside.mkdir()
        git_root = tmp_path / "repo"
        git_root.mkdir()
        ws = Workspace(
            lock_path=outside / "uv.lock",
            workspace_root=outside,
            graph=DependencyGraph(),
        )
        assert _workspace_label(ws, git_root) == str(outside)

    def test_source_display_path_no_label(self):
        assert _source_display_path("", "packages/api") == "packages/api"

    def test_source_display_path_virtual_root(self):
        """Virtual root (source_path='.') displays as just the workspace label."""
        assert _source_display_path("python", ".") == "python"

    def test_source_display_path_qualified(self):
        assert _source_display_path("python", "packages/api") == "python/packages/api"


class TestPrintHuman:
    def test_no_affected(self, capsys):
        result = {
            "directly_changed": [],
            "affected": [],
            "test_all": False,
            "file_mapping": {},
        }
        _print_human(result)
        out = capsys.readouterr().out
        assert "No affected packages" in out

    def test_with_affected(self, capsys):
        result = {
            "directly_changed": ["api"],
            "affected": ["api", "shared"],
            "test_all": False,
            "file_mapping": {},
        }
        _print_human(result)
        out = capsys.readouterr().out
        assert "Affected packages (2)" in out
        assert "api (direct)" in out
        assert "shared (transitive)" in out

    def test_test_all_banner(self, capsys):
        result = {
            "directly_changed": [],
            "affected": ["api"],
            "test_all": True,
            "file_mapping": {},
        }
        _print_human(result)
        out = capsys.readouterr().out
        assert "Testing all packages" in out

    def test_detailed_with_mapping(self, capsys):
        result = {
            "directly_changed": ["api"],
            "affected": ["api"],
            "test_all": False,
            "file_mapping": {
                "packages/api/main.py": "api",
                "README.md": None,
            },
        }
        _print_human(result, detailed=True)
        out = capsys.readouterr().out
        assert "Changed files (2)" in out
        assert "packages/api/main.py" in out
        assert "-> api" in out
        assert "(root/unmatched)" in out

    def test_direct_and_transitive_markers(self, capsys):
        result = {
            "directly_changed": ["shared"],
            "affected": ["api", "shared"],
            "test_all": False,
            "file_mapping": {},
        }
        _print_human(result)
        out = capsys.readouterr().out
        assert "shared (direct)" in out
        assert "api (transitive)" in out
