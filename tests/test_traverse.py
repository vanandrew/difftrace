from difftrace.traverse import find_affected_packages


class TestFindAffectedPackages:
    def test_leaf_change(self):
        """Changing a leaf package only affects itself."""
        reverse = {"shared": {"api", "worker"}, "api": {"app"}}
        result = find_affected_packages({"app"}, reverse)
        assert result == {"app"}

    def test_direct_dependents(self):
        """Changing shared affects api and worker directly."""
        reverse = {"shared": {"api", "worker"}}
        result = find_affected_packages({"shared"}, reverse)
        assert result == {"shared", "api", "worker"}

    def test_transitive_diamond(self):
        """Changing shared propagates through diamond: shared → api,worker → app."""
        reverse = {
            "shared": {"api", "worker"},
            "api": {"app"},
            "worker": {"app"},
        }
        result = find_affected_packages({"shared"}, reverse)
        assert result == {"shared", "api", "worker", "app"}

    def test_multiple_starts(self):
        """Multiple directly changed packages are all included."""
        reverse = {"shared": {"api"}, "worker": {"app"}}
        result = find_affected_packages({"shared", "worker"}, reverse)
        assert result == {"shared", "api", "worker", "app"}

    def test_empty_input(self):
        """No changed packages → no affected packages."""
        reverse = {"shared": {"api"}}
        result = find_affected_packages(set(), reverse)
        assert result == set()

    def test_no_reverse_deps(self):
        """Package with no dependents only returns itself."""
        reverse: dict[str, set[str]] = {}
        result = find_affected_packages({"standalone"}, reverse)
        assert result == {"standalone"}

    def test_cycle(self):
        """Cycles in the dependency graph don't cause infinite loops."""
        reverse = {"a": {"b"}, "b": {"a"}}
        result = find_affected_packages({"a"}, reverse)
        assert result == {"a", "b"}

    def test_three_node_cycle(self):
        """A→B→C→A terminates correctly."""
        reverse = {"a": {"b"}, "b": {"c"}, "c": {"a"}}
        result = find_affected_packages({"a"}, reverse)
        assert result == {"a", "b", "c"}

    def test_large_cycle(self):
        """5-node cycle terminates correctly."""
        reverse = {
            "a": {"b"},
            "b": {"c"},
            "c": {"d"},
            "d": {"e"},
            "e": {"a"},
        }
        result = find_affected_packages({"a"}, reverse)
        assert result == {"a", "b", "c", "d", "e"}
