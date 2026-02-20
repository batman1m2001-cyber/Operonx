"""Tests for CompiledGraph via PyO3 bindings."""

import pytest

from hush_runtime import CompiledGraph


class TestFromDict:
    """Test CompiledGraph construction from Python dicts."""

    def test_from_dict_basic(self, linear_graph_data):
        sched = CompiledGraph.from_dict(linear_graph_data)
        assert sched.num_ops() == 3

    def test_from_dict_missing_key_raises(self):
        with pytest.raises(KeyError):
            CompiledGraph.from_dict({"adjacency": {}})

    def test_entries(self, linear_graph_data):
        sched = CompiledGraph.from_dict(linear_graph_data)
        entries = sched.get_entries()
        assert entries == [("A", True)]

    def test_entries_parallel(self, parallel_fork_join_data):
        sched = CompiledGraph.from_dict(parallel_fork_join_data)
        entries = sched.get_entries()
        names = [name for name, _ in entries]
        assert "A" in names
        assert "B" in names


class TestLinearChain:
    """Test linear chain scheduling: A -> B -> C."""

    def test_activation_order(self, linear_graph_data):
        sched = CompiledGraph.from_dict(linear_graph_data)
        state = sched.new_run_state()

        ready = sched.activate_successors("A", state, None)
        assert ready == [("B", True)]

        ready = sched.activate_successors("B", state, None)
        assert ready == [("C", True)]

        ready = sched.activate_successors("C", state, None)
        assert ready == []


class TestParallelForkJoin:
    """Test fork-join: A, B -> C."""

    def test_join_waits_for_both(self, parallel_fork_join_data):
        sched = CompiledGraph.from_dict(parallel_fork_join_data)
        state = sched.new_run_state()

        # A completes — C not ready
        ready = sched.activate_successors("A", state, None)
        assert ready == []

        # B completes — now C is ready
        ready = sched.activate_successors("B", state, None)
        assert ready == [("C", True)]

    def test_join_order_independent(self, parallel_fork_join_data):
        """C should be ready regardless of which predecessor completes first."""
        sched = CompiledGraph.from_dict(parallel_fork_join_data)
        state = sched.new_run_state()

        # B completes first
        ready = sched.activate_successors("B", state, None)
        assert ready == []

        # Then A
        ready = sched.activate_successors("A", state, None)
        assert ready == [("C", True)]


class TestDiamondGraph:
    """Test diamond: A -> B, C -> D."""

    def test_diamond_activation(self, diamond_graph_data):
        sched = CompiledGraph.from_dict(diamond_graph_data)
        state = sched.new_run_state()

        # A completes: B and C become ready
        ready = sched.activate_successors("A", state, None)
        assert len(ready) == 2
        names = {name for name, _ in ready}
        assert names == {"B", "C"}

        # B completes: D not ready yet (needs C too)
        ready = sched.activate_successors("B", state, None)
        assert ready == []

        # C completes: D now ready
        ready = sched.activate_successors("C", state, None)
        assert ready == [("D", True)]


class TestBranchOp:
    """Test branch ops with soft edges."""

    def test_branch_selects_target(self, branch_graph_data):
        sched = CompiledGraph.from_dict(branch_graph_data)
        state = sched.new_run_state()

        # Branch selects case_a
        ready = sched.activate_successors("branch", state, "case_a")
        assert ready == [("case_a", True)]

    def test_branch_selects_other_target(self, branch_graph_data):
        sched = CompiledGraph.from_dict(branch_graph_data)
        state = sched.new_run_state()

        # Branch selects case_b
        ready = sched.activate_successors("branch", state, "case_b")
        assert ready == [("case_b", True)]

    def test_branch_none_target_activates_nothing(self, branch_graph_data):
        sched = CompiledGraph.from_dict(branch_graph_data)
        state = sched.new_run_state()

        # Branch with no target (e.g., END)
        ready = sched.activate_successors("branch", state, None)
        assert ready == []

    def test_soft_edge_merge(self, branch_graph_data):
        sched = CompiledGraph.from_dict(branch_graph_data)
        state = sched.new_run_state()

        # Branch -> case_a -> merge
        sched.activate_successors("branch", state, "case_a")
        ready = sched.activate_successors("case_a", state, None)
        assert ready == [("merge", True)]

    def test_soft_edge_counts_once(self, branch_graph_data):
        """If both case_a and case_b complete, merge only activates once."""
        # Modify: both branches are activated (unusual but tests soft semantics)
        data = {
            "adjacency": {
                "case_a": [("merge", True)],
                "case_b": [("merge", True)],
                "merge": [],
            },
            "ready_count": {"case_a": 0, "case_b": 0, "merge": 1},
            "entries": ["case_a", "case_b"],
            "can_inline": {"case_a": True, "case_b": True, "merge": True},
            "is_branch": {},
        }
        sched = CompiledGraph.from_dict(data)
        state = sched.new_run_state()

        # First soft predecessor -> merge activates
        ready = sched.activate_successors("case_a", state, None)
        assert ready == [("merge", True)]

        # Second soft predecessor -> merge already satisfied
        ready = sched.activate_successors("case_b", state, None)
        assert ready == []

    def test_is_branch_op(self, branch_graph_data):
        sched = CompiledGraph.from_dict(branch_graph_data)
        assert sched.is_branch_op("branch") is True
        assert sched.is_branch_op("case_a") is False
        assert sched.is_branch_op("nonexistent") is False


class TestCanInline:
    """Test the can_inline flag in scheduler results."""

    def test_inline_flag_true(self, linear_graph_data):
        sched = CompiledGraph.from_dict(linear_graph_data)
        entries = sched.get_entries()
        assert entries == [("A", True)]

    def test_inline_flag_false(self):
        data = {
            "adjacency": {"A": [("B", False)], "B": []},
            "ready_count": {"A": 0, "B": 1},
            "entries": ["A"],
            "can_inline": {"A": True, "B": False},
            "is_branch": {},
        }
        sched = CompiledGraph.from_dict(data)
        state = sched.new_run_state()
        ready = sched.activate_successors("A", state, None)
        assert ready == [("B", False)]


class TestRunStateIndependence:
    """Test that each run state is independent."""

    def test_independent_states(self, linear_graph_data):
        sched = CompiledGraph.from_dict(linear_graph_data)
        state1 = sched.new_run_state()
        state2 = sched.new_run_state()

        # Activate in state1
        ready1 = sched.activate_successors("A", state1, None)
        assert ready1 == [("B", True)]

        # state2 is unaffected — B still needs A
        ready2 = sched.activate_successors("A", state2, None)
        assert ready2 == [("B", True)]

    def test_many_states(self, linear_graph_data):
        """Create many run states concurrently."""
        sched = CompiledGraph.from_dict(linear_graph_data)
        states = [sched.new_run_state() for _ in range(100)]

        for s in states:
            ready = sched.activate_successors("A", s, None)
            assert ready == [("B", True)]


class TestLargeGraph:
    """Test with larger graphs for correctness."""

    def test_wide_fanout(self):
        """A -> B0, B1, ..., B99 -> C (C waits for all)."""
        n = 100
        adj = {"A": [(f"B{i}", False) for i in range(n)]}
        rc = {"A": 0, "C": n}
        ci = {"A": True, "C": True}
        for i in range(n):
            name = f"B{i}"
            adj[name] = [("C", False)]
            rc[name] = 1
            ci[name] = True

        data = {
            "adjacency": adj,
            "ready_count": rc,
            "entries": ["A"],
            "can_inline": ci,
            "is_branch": {},
        }
        sched = CompiledGraph.from_dict(data)
        state = sched.new_run_state()

        # A completes: all B ops ready
        ready = sched.activate_successors("A", state, None)
        assert len(ready) == n

        # Complete all B ops except last
        for i in range(n - 1):
            ready = sched.activate_successors(f"B{i}", state, None)
            assert ready == []

        # Last B completes: C is ready
        ready = sched.activate_successors(f"B{n - 1}", state, None)
        assert ready == [("C", True)]

    def test_long_linear_chain(self):
        """Chain of 1000 ops."""
        n = 1000
        names = [f"op{i}" for i in range(n)]
        adj = {}
        rc = {}
        ci = {}

        for i, name in enumerate(names):
            if i < n - 1:
                adj[name] = [(names[i + 1], False)]
            else:
                adj[name] = []
            rc[name] = 0 if i == 0 else 1
            ci[name] = True

        data = {
            "adjacency": adj,
            "ready_count": rc,
            "entries": [names[0]],
            "can_inline": ci,
            "is_branch": {},
        }
        sched = CompiledGraph.from_dict(data)
        state = sched.new_run_state()

        # Walk the chain
        for i in range(n - 1):
            ready = sched.activate_successors(names[i], state, None)
            assert len(ready) == 1
            assert ready[0][0] == names[i + 1]

        # Last op has no successors
        ready = sched.activate_successors(names[-1], state, None)
        assert ready == []


class TestMixedHardSoftEdges:
    """Test graphs with both hard and soft edges."""

    def test_hard_plus_soft(self):
        """A (hard) -> D, B (soft) -> D, C (soft) -> D.
        ready_count[D] = 2 (1 hard + 1 soft group).
        """
        data = {
            "adjacency": {
                "A": [("D", False)],
                "B": [("D", True)],
                "C": [("D", True)],
                "D": [],
            },
            "ready_count": {"A": 0, "B": 0, "C": 0, "D": 2},
            "entries": ["A", "B", "C"],
            "can_inline": {"A": True, "B": True, "C": True, "D": True},
            "is_branch": {},
        }
        sched = CompiledGraph.from_dict(data)
        state = sched.new_run_state()

        # A (hard) completes: D still needs soft
        ready = sched.activate_successors("A", state, None)
        assert ready == []

        # B (soft) completes: D now has both hard + soft satisfied
        ready = sched.activate_successors("B", state, None)
        assert ready == [("D", True)]

        # C (soft) completes: D already counted
        ready = sched.activate_successors("C", state, None)
        assert ready == []
