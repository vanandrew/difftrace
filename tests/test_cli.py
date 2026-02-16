from __future__ import annotations

import json
import subprocess
from unittest.mock import patch

import pytest

from difftrace.cli import (
    _parse_triggers,
    _print_human,
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
        assert args.lock_file == "uv.lock"

    def test_custom_lock_file(self):
        parser = build_parser()
        args = parser.parse_args(["--lock-file", "custom.lock"])
        assert args.lock_file == "custom.lock"

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

    def test_defaults(self):
        parser = build_parser()
        args = parser.parse_args([])
        assert args.json_output is False
        assert args.names is False
        assert args.paths is False
        assert args.no_dev is False
        assert args.no_optional is False
        assert args.direct_only is False
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
        root, dirs = _parse_triggers(["Dockerfile"])
        assert "Dockerfile" in root
        assert root >= DEFAULT_ROOT_TRIGGERS

    def test_dir_trigger(self):
        root, dirs = _parse_triggers(["docker/"])
        assert "docker/" in dirs
        assert dirs >= DEFAULT_DIR_TRIGGERS

    def test_mixed_triggers(self):
        root, dirs = _parse_triggers(["Dockerfile", "docker/"])
        assert "Dockerfile" in root
        assert "docker/" in dirs


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
        mock_run.side_effect = [git_root_result, diff_result]

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
        mock_run.side_effect = [git_root_result, diff_result]

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
        mock_run.side_effect = [git_root_result, diff_result]

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
        mock_run.side_effect = [git_root_result, diff_result]

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
        mock_run.side_effect = [git_root_result, diff_result]

        args = self._make_args(tmp_path, exclude=["api"])
        result = run(args)
        # shared changed → api, worker are transitive. api is excluded.
        assert "api" not in result["affected"]
        assert "shared" in result["affected"]
        assert "worker" in result["affected"]

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
        mock_run.side_effect = [git_root_result, diff_result]

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
        mock_run.side_effect = [git_root_result, diff_result]

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
        with patch("sys.argv", ["difftrace"]):
            with pytest.raises(SystemExit, match="1"):
                main()
        err = capsys.readouterr().err
        assert "uv.lock not found" in err

    @patch("difftrace.cli.run")
    def test_error_timeout(self, mock_run, capsys):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="git", timeout=30)
        with patch("sys.argv", ["difftrace"]):
            with pytest.raises(SystemExit, match="1"):
                main()

    @patch("difftrace.cli.run")
    def test_error_called_process_error(self, mock_run, capsys):
        mock_run.side_effect = subprocess.CalledProcessError(returncode=1, cmd="git")
        with patch("sys.argv", ["difftrace"]):
            with pytest.raises(SystemExit, match="1"):
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
        mock_run.side_effect = [git_root_result, diff_result]

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
        mock_run.side_effect = [git_root_result, diff_result]

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
        mock_run.side_effect = [git_root_result, diff_result]

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
        assert "Root config changed" in out

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
