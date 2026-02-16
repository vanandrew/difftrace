import pytest

from difftrace.graph import parse_lock_file


class TestParseLockFile:
    def test_simple_workspace_packages(self, simple_lock):
        graph = parse_lock_file(simple_lock)
        assert set(graph.packages.keys()) == {"api", "shared", "worker"}

    def test_simple_source_paths(self, simple_lock):
        graph = parse_lock_file(simple_lock)
        assert graph.packages["api"].source_path == "packages/api"
        assert graph.packages["shared"].source_path == "packages/shared"
        assert graph.packages["worker"].source_path == "packages/worker"

    def test_external_deps_excluded(self, simple_lock):
        graph = parse_lock_file(simple_lock)
        # "requests" is not a workspace member and should not appear
        assert "requests" not in graph.packages
        assert "requests" not in graph.forward.get("api", set())

    def test_forward_edges(self, simple_lock):
        graph = parse_lock_file(simple_lock)
        assert graph.forward["api"] == {"shared"}
        assert graph.forward["worker"] == {"shared"}
        assert graph.forward.get("shared", set()) == set()

    def test_reverse_edges(self, simple_lock):
        graph = parse_lock_file(simple_lock)
        assert graph.reverse["shared"] == {"api", "worker"}
        assert graph.reverse.get("api", set()) == set()

    def test_diamond_dependencies(self, diamond_lock):
        graph = parse_lock_file(diamond_lock)
        assert graph.forward["app"] == {"api", "worker"}
        assert graph.forward["api"] == {"shared"}
        assert graph.forward["worker"] == {"shared"}
        assert graph.reverse["shared"] == {"api", "worker"}
        assert graph.reverse["api"] == {"app"}
        assert graph.reverse["worker"] == {"app"}

    def test_virtual_root(self, virtual_root_lock):
        graph = parse_lock_file(virtual_root_lock)
        assert graph.packages["myproject"].source_path == "."
        assert graph.forward["myproject"] == {"api", "lib"}

    def test_optional_deps_included(self, optional_dev_lock):
        graph = parse_lock_file(optional_dev_lock, include_optional=True)
        assert "worker" in graph.forward["api"]

    def test_optional_deps_excluded(self, optional_dev_lock):
        graph = parse_lock_file(
            optional_dev_lock, include_dev=False, include_optional=False
        )
        assert graph.forward["api"] == {"shared"}

    def test_dev_deps_included(self, optional_dev_lock):
        graph = parse_lock_file(optional_dev_lock, include_dev=True)
        assert "worker" in graph.forward["api"]

    def test_dev_deps_excluded(self, optional_dev_lock):
        graph = parse_lock_file(
            optional_dev_lock, include_dev=False, include_optional=False
        )
        assert "worker" not in graph.forward.get("api", set())

    def test_missing_file(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            parse_lock_file(tmp_path / "nonexistent.lock")

    def test_no_manifest(self, tmp_path):
        lock_file = tmp_path / "uv.lock"
        lock_file.write_text('version = 1\n\n[[package]]\nname = "foo"\nversion = "1.0"\n')
        with pytest.raises(ValueError, match="no \\[manifest\\] section"):
            parse_lock_file(lock_file)

    def test_empty_members(self, tmp_path):
        lock_file = tmp_path / "uv.lock"
        lock_file.write_text("version = 1\n\n[manifest]\nmembers = []\n")
        with pytest.raises(ValueError, match="no workspace members"):
            parse_lock_file(lock_file)
