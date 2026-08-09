"""Tests for InMemoryCheckpointer + Checkpointer protocol."""

import pytest

from operonx.checkpoint import (
    CellWriteEvent,
    Checkpointer,
    InMemoryCheckpointer,
    StepEvent,
    StepNotFound,
)


def _write(step, op, var, ctx, value):
    return CellWriteEvent(step_id=step, op=op, var=var, ctx=ctx, value=value)


class TestProtocolConformance:
    def test_in_memory_satisfies_checkpointer_protocol(self):
        cp = InMemoryCheckpointer()
        assert isinstance(cp, Checkpointer)


class TestBasicRecordAndReplay:
    def test_no_writes_no_steps(self):
        cp = InMemoryCheckpointer()
        assert cp.list_steps() == []

    def test_single_write_appears_in_updates_and_state(self):
        cp = InMemoryCheckpointer()
        cp.on_cell_write(_write(0, "op", "x", ("main",), 42))

        assert cp.list_steps() == [0]
        assert cp.get_updates(0) == {("op", "x", ("main",)): 42}
        assert cp.get_state(0) == {("op", "x", ("main",)): 42}

    def test_updates_returns_only_that_step_delta(self):
        cp = InMemoryCheckpointer()
        cp.on_cell_write(_write(0, "op", "x", ("main",), 1))
        cp.on_cell_write(_write(1, "op", "x", ("main",), 2))
        cp.on_cell_write(_write(2, "op", "x", ("main",), 3))

        assert cp.get_updates(0) == {("op", "x", ("main",)): 1}
        assert cp.get_updates(1) == {("op", "x", ("main",)): 2}
        assert cp.get_updates(2) == {("op", "x", ("main",)): 3}

    def test_state_folds_deltas_up_to_step(self):
        cp = InMemoryCheckpointer()
        cp.on_cell_write(_write(0, "op", "x", ("main",), 1))
        cp.on_cell_write(_write(1, "op", "y", ("main",), "hello"))
        cp.on_cell_write(_write(2, "op", "x", ("main",), 2))  # overwrites x

        state = cp.get_state(2)
        assert state == {
            ("op", "x", ("main",)): 2,
            ("op", "y", ("main",)): "hello",
        }

    def test_state_at_earlier_step_omits_later_writes(self):
        cp = InMemoryCheckpointer()
        cp.on_cell_write(_write(0, "op", "x", ("main",), 1))
        cp.on_cell_write(_write(5, "op", "y", ("main",), "future"))

        state_at_0 = cp.get_state(0)
        assert state_at_0 == {("op", "x", ("main",)): 1}

    def test_multiple_writes_same_step_all_captured(self):
        cp = InMemoryCheckpointer()
        cp.on_cell_write(_write(0, "op", "x", ("main",), 1))
        cp.on_cell_write(_write(0, "op", "y", ("main",), 2))
        cp.on_cell_write(_write(0, "op2", "z", ("main",), 3))

        assert cp.get_updates(0) == {
            ("op", "x", ("main",)): 1,
            ("op", "y", ("main",)): 2,
            ("op2", "z", ("main",)): 3,
        }


class TestErrorCases:
    def test_get_state_beyond_max_step_raises(self):
        cp = InMemoryCheckpointer()
        cp.on_cell_write(_write(2, "op", "x", ("main",), 42))
        with pytest.raises(StepNotFound) as exc_info:
            cp.get_state(step=99)
        assert exc_info.value.step == 99
        assert exc_info.value.max_step == 2

    def test_get_updates_beyond_max_raises(self):
        cp = InMemoryCheckpointer()
        cp.on_cell_write(_write(0, "op", "x", ("main",), 1))
        with pytest.raises(StepNotFound):
            cp.get_updates(step=10)

    def test_get_updates_at_recorded_but_empty_step_returns_empty(self):
        cp = InMemoryCheckpointer()
        cp.on_step(StepEvent(step_id=0, op="op"))  # boundary, no writes
        assert cp.get_updates(0) == {}


class TestStepBoundaries:
    def test_step_event_registers_step_even_without_writes(self):
        cp = InMemoryCheckpointer()
        cp.on_step(StepEvent(step_id=0, op="setup"))
        cp.on_step(StepEvent(step_id=1, op="teardown"))
        assert cp.list_steps() == [0, 1]

    def test_step_event_and_writes_agree(self):
        cp = InMemoryCheckpointer()
        cp.on_cell_write(_write(0, "op", "x", ("main",), 1))
        cp.on_step(StepEvent(step_id=0, op="op"))
        cp.on_cell_write(_write(1, "op", "x", ("main",), 2))
        cp.on_step(StepEvent(step_id=1, op="op"))
        assert cp.list_steps() == [0, 1]


class TestSampling:
    def test_sample_every_records_only_matching_steps(self):
        cp = InMemoryCheckpointer(sample_every=3)
        for step in range(10):
            cp.on_cell_write(_write(step, "op", "x", ("main",), step))
        # Steps 0, 3, 6, 9 recorded; 1,2,4,5,7,8 dropped.
        assert cp.list_steps() == [0, 3, 6, 9]
        assert cp.get_updates(3) == {("op", "x", ("main",)): 3}

    def test_sample_every_none_records_all(self):
        cp = InMemoryCheckpointer(sample_every=None)
        for step in range(5):
            cp.on_cell_write(_write(step, "op", "x", ("main",), step))
        assert cp.list_steps() == [0, 1, 2, 3, 4]

    def test_sample_every_state_still_folds_correctly(self):
        cp = InMemoryCheckpointer(sample_every=2)
        cp.on_cell_write(_write(0, "op", "x", ("main",), 1))
        cp.on_cell_write(_write(1, "op", "x", ("main",), 2))  # dropped
        cp.on_cell_write(_write(2, "op", "x", ("main",), 3))
        # get_state(2) folds recorded 0 → 2; 1's write is invisible.
        assert cp.get_state(2) == {("op", "x", ("main",)): 3}


class TestCancellation:
    def test_on_cancel_recorded(self):
        cp = InMemoryCheckpointer()
        cp.on_cell_write(_write(3, "op", "x", ("main",), 42))
        cp.on_cancel(("main", "spec[1]"))
        assert cp.list_cancels() == {("main", "spec[1]"): 3}

    def test_on_cancel_does_not_rewind_state(self):
        cp = InMemoryCheckpointer()
        cp.on_cell_write(_write(0, "op", "x", ("main", "spec[1]"), 42))
        cp.on_cancel(("main", "spec[1]"))
        # State preserved for audit trail — cancellation is a separate signal.
        assert cp.get_state(0) == {("op", "x", ("main", "spec[1]")): 42}


class TestPerCtxCells:
    """Cell keys are (op, var, ctx) — parallel ctxs are separate entries."""

    def test_same_op_var_different_ctx_kept_apart(self):
        cp = InMemoryCheckpointer()
        cp.on_cell_write(_write(0, "worker", "result", ("main", "[0]"), "a"))
        cp.on_cell_write(_write(0, "worker", "result", ("main", "[1]"), "b"))
        cp.on_cell_write(_write(0, "worker", "result", ("main", "[2]"), "c"))

        assert cp.get_state(0) == {
            ("worker", "result", ("main", "[0]")): "a",
            ("worker", "result", ("main", "[1]")): "b",
            ("worker", "result", ("main", "[2]")): "c",
        }
