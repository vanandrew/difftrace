"""Tests for the action.yml shell logic.

Validates the bash/Python one-liners that parse `difftrace --json` output
and produce the GitHub Action outputs (affected, matrix, has_affected, test_all).
"""

import json
import subprocess
import sys

import pytest

# The Python one-liners from action.yml, extracted for testing.
# These mirror the exact logic in the "Run difftrace" composite step.
EXTRACT_AFFECTED = (
    "import sys, json; print(json.dumps(json.load(sys.stdin)['affected']))"
)
EXTRACT_TEST_ALL = (
    "import sys, json; print(str(json.load(sys.stdin)['test_all']).lower())"
)
EXTRACT_MATRIX = (
    "import sys, json; print(json.dumps({'package': json.load(sys.stdin)['affected']}))"
)


def _run_python_oneliner(code: str, stdin_data: str) -> str:
    """Run a Python one-liner with the given stdin, return stripped stdout."""
    result = subprocess.run(
        [sys.executable, "-c", code],
        input=stdin_data,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


class TestActionOutputParsing:
    """Test the Python one-liners that parse difftrace JSON in action.yml."""

    def test_affected_with_packages(self):
        result_json = json.dumps(
            {
                "directly_changed": ["api"],
                "affected": ["api", "shared"],
                "test_all": False,
            }
        )
        affected = _run_python_oneliner(EXTRACT_AFFECTED, result_json)
        assert json.loads(affected) == ["api", "shared"]

    def test_affected_empty(self):
        result_json = json.dumps(
            {"directly_changed": [], "affected": [], "test_all": False}
        )
        affected = _run_python_oneliner(EXTRACT_AFFECTED, result_json)
        assert json.loads(affected) == []

    def test_test_all_true(self):
        result_json = json.dumps(
            {
                "directly_changed": [],
                "affected": ["api", "shared", "worker"],
                "test_all": True,
            }
        )
        test_all = _run_python_oneliner(EXTRACT_TEST_ALL, result_json)
        assert test_all == "true"

    def test_test_all_false(self):
        result_json = json.dumps(
            {"directly_changed": [], "affected": [], "test_all": False}
        )
        test_all = _run_python_oneliner(EXTRACT_TEST_ALL, result_json)
        assert test_all == "false"

    def test_matrix_with_packages(self):
        result_json = json.dumps(
            {
                "directly_changed": ["api"],
                "affected": ["api", "worker"],
                "test_all": False,
            }
        )
        matrix = _run_python_oneliner(EXTRACT_MATRIX, result_json)
        assert json.loads(matrix) == {"package": ["api", "worker"]}

    def test_matrix_empty(self):
        result_json = json.dumps(
            {"directly_changed": [], "affected": [], "test_all": False}
        )
        matrix = _run_python_oneliner(EXTRACT_MATRIX, result_json)
        assert json.loads(matrix) == {"package": []}


class TestActionHasAffectedLogic:
    """Test the bash has_affected logic, replicated in Python."""

    @pytest.mark.parametrize(
        ("affected", "expected"),
        [
            (["api", "shared"], "true"),
            (["api"], "true"),
            ([], "false"),
        ],
    )
    def test_has_affected(self, affected: list[str], expected: str):
        # Mirror the bash logic: if [ "$affected" = "[]" ]; then "false" else "true"
        affected_json = json.dumps(affected)
        has_affected = "false" if affected_json == "[]" else "true"
        assert has_affected == expected


class TestActionFullFlow:
    """Test the complete action output derivation from a difftrace JSON result."""

    def _derive_action_outputs(self, difftrace_json: str) -> dict[str, str]:
        """Simulate the full action.yml step 3 logic."""
        affected = _run_python_oneliner(EXTRACT_AFFECTED, difftrace_json)
        test_all = _run_python_oneliner(EXTRACT_TEST_ALL, difftrace_json)
        matrix = _run_python_oneliner(EXTRACT_MATRIX, difftrace_json)
        has_affected = "false" if affected == "[]" else "true"
        return {
            "affected": affected,
            "test_all": test_all,
            "matrix": matrix,
            "has_affected": has_affected,
        }

    def test_typical_change(self):
        result = json.dumps(
            {
                "directly_changed": ["shared"],
                "affected": ["api", "shared", "worker"],
                "test_all": False,
            }
        )
        outputs = self._derive_action_outputs(result)
        assert json.loads(outputs["affected"]) == ["api", "shared", "worker"]
        assert json.loads(outputs["matrix"]) == {"package": ["api", "shared", "worker"]}
        assert outputs["has_affected"] == "true"
        assert outputs["test_all"] == "false"

    def test_no_changes(self):
        result = json.dumps(
            {
                "directly_changed": [],
                "affected": [],
                "test_all": False,
            }
        )
        outputs = self._derive_action_outputs(result)
        assert json.loads(outputs["affected"]) == []
        assert json.loads(outputs["matrix"]) == {"package": []}
        assert outputs["has_affected"] == "false"
        assert outputs["test_all"] == "false"

    def test_root_trigger(self):
        result = json.dumps(
            {
                "directly_changed": [],
                "affected": ["api", "shared", "worker"],
                "test_all": True,
            }
        )
        outputs = self._derive_action_outputs(result)
        assert json.loads(outputs["affected"]) == ["api", "shared", "worker"]
        assert outputs["has_affected"] == "true"
        assert outputs["test_all"] == "true"

    def test_matrix_usable_by_github(self):
        """Ensure matrix output is valid for `strategy.matrix: ${{ fromJson(...) }}`."""
        result = json.dumps(
            {
                "directly_changed": ["api"],
                "affected": ["api"],
                "test_all": False,
            }
        )
        outputs = self._derive_action_outputs(result)
        matrix = json.loads(outputs["matrix"])
        # GitHub Actions expects {"package": [...]} shape
        assert "package" in matrix
        assert isinstance(matrix["package"], list)
