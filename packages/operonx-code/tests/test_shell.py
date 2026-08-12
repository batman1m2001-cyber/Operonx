"""The persistent shell.

The property that matters is the one a fresh-subprocess implementation
silently lacks: state carries between calls. A model that runs `cd src`
and then `ls` has no way to see that its `cd` was discarded — it just
reads a confusing result and doubts itself.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from operonx_code.shell import PersistentShell, ShellTimeout

pytestmark = pytest.mark.unit


@pytest.fixture
async def shell(tmp_path: Path):
    sh = PersistentShell(cwd=tmp_path)
    yield sh
    await sh.close()


class TestBasics:
    async def test_runs_a_command(self, shell):
        result = await shell.run("echo hello")
        assert result.output == "hello"
        assert result.exit_code == 0

    async def test_reports_a_nonzero_exit(self, shell):
        result = await shell.run("exit 3 || true; false")
        assert result.exit_code != 0

    async def test_stderr_is_interleaved_with_stdout(self, shell):
        """A traceback is only useful next to the command that produced it."""
        result = await shell.run("echo out; echo err 1>&2")
        assert "out" in result.output and "err" in result.output

    async def test_a_failing_command_still_returns_its_output(self, shell):
        result = await shell.run("echo before; ls /definitely-not-here")
        assert "before" in result.output
        assert result.exit_code != 0


class TestPersistence:
    async def test_cd_carries_over(self, shell, tmp_path):
        (tmp_path / "sub").mkdir()
        await shell.run("cd sub")
        result = await shell.run("pwd")
        assert result.output.endswith("sub")

    async def test_exported_variables_carry_over(self, shell):
        await shell.run("export TOKEN=abc123")
        assert (await shell.run("echo $TOKEN")).output == "abc123"

    async def test_shell_functions_carry_over(self, shell):
        await shell.run("greet() { echo hi; }")
        assert (await shell.run("greet")).output == "hi"

    async def test_cwd_is_reported_back(self, shell, tmp_path):
        """So the model can see where its own cd left it."""
        (tmp_path / "deep").mkdir()
        result = await shell.run("cd deep")
        assert result.cwd.endswith("deep")


class TestTimeout:
    async def test_a_slow_command_raises(self, shell):
        with pytest.raises(ShellTimeout):
            await shell.run("sleep 5", timeout=0.4)

    async def test_the_shell_works_again_afterwards(self, shell):
        with pytest.raises(ShellTimeout):
            await shell.run("sleep 5", timeout=0.4)
        assert (await shell.run("echo alive")).output == "alive"

    async def test_the_message_says_state_was_lost(self, shell):
        """Silently starting fresh is how an agent ends up confused about
        its own cd — the loss has to be stated."""
        await shell.run("export KEEP=1")
        with pytest.raises(ShellTimeout, match="cd/export"):
            await shell.run("sleep 5", timeout=0.4)
        assert (await shell.run("echo [$KEEP]")).output == "[]"


class TestOutputClamp:
    async def test_large_output_is_truncated_head_and_tail(self, tmp_path):
        """A build log's first lines say what ran and its last say why it
        failed; the middle is the part nobody reads."""
        sh = PersistentShell(cwd=tmp_path, max_output=400)
        try:
            result = await sh.run("seq 1 5000")
            assert result.truncated is True
            assert "omitted" in result.output
            assert result.output.startswith("1\n")
            assert result.output.rstrip().endswith("5000")
        finally:
            await sh.close()

    async def test_small_output_is_untouched(self, shell):
        result = await shell.run("echo small")
        assert result.truncated is False
        assert "omitted" not in result.output


class TestConcurrency:
    async def test_concurrent_calls_do_not_interleave(self, shell):
        """One pipe, two commands: without the lock their output mixes and
        neither caller can tell which lines are theirs."""
        results = await asyncio.gather(
            shell.run("echo aaa"),
            shell.run("echo bbb"),
            shell.run("echo ccc"),
        )
        assert sorted(r.output for r in results) == ["aaa", "bbb", "ccc"]


class TestLifecycle:
    async def test_close_is_idempotent(self, tmp_path):
        sh = PersistentShell(cwd=tmp_path)
        await sh.run("echo x")
        await sh.close()
        await sh.close()

    async def test_it_restarts_after_close(self, tmp_path):
        sh = PersistentShell(cwd=tmp_path)
        try:
            await sh.run("echo one")
            await sh.close()
            assert (await sh.run("echo two")).output == "two"
        finally:
            await sh.close()

    async def test_a_shell_that_exits_is_reported_not_hung(self, shell):
        """`exit` closes stdout; the reader must return rather than wait
        forever for a marker that will never arrive."""
        result = await asyncio.wait_for(shell.run("exit 0"), timeout=10)
        assert result.exit_code == -1
        assert "fresh one was started" in result.output

    async def test_the_shell_recovers_from_an_exit(self, shell):
        """A model that runs a script ending in `exit` must not lose the
        session for every command afterwards."""
        await shell.run("exit 0")
        assert (await shell.run("echo alive")).output == "alive"
