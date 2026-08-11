"""Security regressions in dispatch and delegation.

Every test here corresponds to a bug that shipped and was found by
adversarial review, not by the tests written alongside the code. The
common shape: the existing tests exercised **one** call, or answered
**every** approval the same way, so a per-call boundary that did not
exist looked like one that did.
"""

from __future__ import annotations

import asyncio

import pytest

from operonx.agents.graphs.dispatch import build_dispatch
from operonx.agents.graphs.react import each_call_of
from operonx.agents.graphs.subagent import make_delegate_tool
from operonx.agents.policy import ToolPolicy
from operonx.agents.redact import Redactor
from operonx.agents.tool import clear_registry, tool
from operonx.checkpoint import bind_interrupt_bus
from operonx.core import END, PARENT, START, Operon, graph, op

pytestmark = pytest.mark.unit

EMPTY = {"type": "object", "properties": {}}
NUM = {"type": "object", "properties": {"a": {"type": "number"}}}


@pytest.fixture
def ran():
    clear_registry()
    executed: list[str] = []
    yield executed
    clear_registry()


class TestParallelApprovalsAreIndependent:
    """Approving one gated call and denying another must not merge.

    The decision travelled through a `PARENT` cell, which is shared
    across contexts by definition — so with calls fanned out, the last
    arm to answer overwrote every sibling. Denying one destructive call
    and approving another ran **both**. Every prior test answered all
    approvals identically, which is exactly why it survived.
    """

    @staticmethod
    def _fan_graph():
        dispatch_one = build_dispatch(approval_timeout=5.0)

        @graph
        def fan(tool_calls=None):
            gen = each_call_of(tool_calls=tool_calls)
            disp = dispatch_one(call=gen["call"].parallel(max=8))
            disp["tool_message"] >> PARENT["msgs"]
            START >> gen >> disp >> END

        return fan(tool_calls=None)

    async def _run(self, calls, answer_for):
        built = self._fan_graph()
        handle = Operon(built).start(inputs={"tool_calls": calls})
        prompts = []

        def sink(evt):
            name = evt.payload["tool"]
            prompts.append(name)
            handle.state.resume_interrupt(evt.interrupt_id, {"approved": answer_for(name)})

        bind_interrupt_bus(handle.state, sink=sink)
        await asyncio.wait_for(handle.result(), timeout=60)
        return prompts

    @pytest.fixture(autouse=True)
    def _tools(self, ran):
        @tool(name="wipeA", description="A.", schema=NUM, destructive=True)
        async def wipe_a(a: float = 0) -> dict:
            ran.append("wipeA")
            return {"ok": 1}

        @tool(name="wipeB", description="B.", schema=NUM, destructive=True)
        async def wipe_b(a: float = 0) -> dict:
            ran.append("wipeB")
            return {"ok": 1}

        @tool(name="safe", description="Safe.", schema=NUM, readonly=True)
        async def safe(a: float = 0) -> dict:
            ran.append("safe")
            return {"ok": 1}

        self.calls = [
            {"id": "1", "name": "wipeA", "args": {"a": 1}},
            {"id": "2", "name": "wipeB", "args": {"a": 2}},
        ]

    @pytest.mark.asyncio
    async def test_denying_one_does_not_run_it(self, ran):
        prompts = await self._run(self.calls, lambda n: n == "wipeB")
        assert sorted(prompts) == ["wipeA", "wipeB"], "both must be asked about"
        assert ran == ["wipeB"], "the denied call must not execute"

    @pytest.mark.asyncio
    async def test_approving_one_does_not_deny_the_other(self, ran):
        """The mirror image — the sibling's denial used to suppress an
        approved call, so the human's yes silently did nothing."""
        await self._run(self.calls, lambda n: n == "wipeA")
        assert ran == ["wipeA"]

    @pytest.mark.asyncio
    async def test_readonly_sibling_is_unaffected_by_a_denial(self, ran):
        calls = self.calls[:1] + [{"id": "3", "name": "safe", "args": {"a": 3}}]
        await self._run(calls, lambda n: False)
        assert ran == ["safe"], "an ungated call must run regardless of a denial"


class TestRedactionCoversEveryPath:
    @pytest.mark.asyncio
    async def test_exception_text_is_scrubbed(self, ran):
        """A stack trace carrying a connection string is the exact case
        redaction exists for, and the error path used to bypass it."""

        @tool(name="boom", description="Raises.", schema=EMPTY)
        async def boom() -> dict:
            raise RuntimeError("postgres://u:supersecretpw123@h/db api_key=sk-ABCDEFGHIJ1234")

        content = await _dispatch_content("boom", redactor=Redactor())
        assert "supersecretpw123" not in content
        assert "sk-ABCDEFGHIJ1234" not in content
        assert "RuntimeError" in content, "the model still needs to know what failed"

    @pytest.mark.asyncio
    async def test_redaction_runs_before_truncation(self, ran):
        """Truncating first cut the END marker off a PEM block, so the
        pattern no longer matched and the key material shipped."""
        pem = (
            "-----BEGIN RSA PRIVATE KEY-----\n"
            + "MIIabc123" * 12
            + "\n-----END RSA PRIVATE KEY-----"
        )

        @tool(name="dump", description="Dump.", schema=EMPTY, max_result_chars=120)
        async def dump() -> dict:
            return {"k": pem}

        content = await _dispatch_content("dump", redactor=Redactor())
        assert "MIIabc123" not in content
        assert "redacted" in content

    @pytest.mark.asyncio
    async def test_truncation_still_applies_after_scrubbing(self, ran):
        @tool(name="big", description="Big.", schema=EMPTY, max_result_chars=60)
        async def big() -> dict:
            return {"blob": "x" * 500}

        content = await _dispatch_content("big", redactor=Redactor())
        assert "[truncated:" in content
        assert len(content) < 200


class TestSynchronousTools:
    @pytest.mark.asyncio
    async def test_a_plain_def_tool_runs(self, ran):
        """`await`ing the result unconditionally failed every call with
        "object dict can't be used in 'await'" — forever, for any tool
        that was not `async def`. Every test fixture happened to be
        async."""

        @tool(name="synctool", description="Sync.", schema=EMPTY)
        def synctool() -> dict:
            ran.append("synctool")
            return {"ok": True}

        content = await _dispatch_content("synctool")
        assert ran == ["synctool"]
        assert "true" in content.lower()


class TestSubAgentToolsetIsEnforced:
    """`allow_tools` was computed and never passed anywhere — the child
    resolved against the global registry and ran tools its parent had
    excluded. The prior test asserted only that the *helper* reported the
    right names, never that a child was actually restricted."""

    @pytest.fixture(autouse=True)
    def _tools(self, ran):
        @tool(name="read", description="Read.", schema=EMPTY, readonly=True)
        async def read() -> dict:
            ran.append("read")
            return {"t": "x"}

        @tool(name="wipe", description="Wipe.", schema=EMPTY, destructive=True)
        async def wipe() -> dict:
            ran.append("wipe")
            return {"gone": True}

    @staticmethod
    def _model(tool_name):
        state = {"i": 0}

        @op
        def child_model(messages: list = None) -> dict:
            i = state["i"]
            state["i"] += 1
            calls = [{"id": "c0", "name": tool_name, "args": {}}] if i == 0 else []
            return {
                "assistant_message": [{"id": f"a{i}", "role": "assistant", "content": "done"}],
                "tool_calls": calls,
                "done": not calls,
            }

        return child_model

    @pytest.mark.asyncio
    async def test_excluded_tool_is_refused(self, ran):
        delegate = make_delegate_tool(
            call_model=self._model("wipe"), allow_tools=["read"], max_turns=4
        )
        await asyncio.wait_for(delegate.__wrapped__(task="go"), timeout=60)
        assert ran == [], "the child must not run a tool outside allow_tools"

    @pytest.mark.asyncio
    async def test_permitted_tool_still_works(self, ran):
        delegate = make_delegate_tool(
            call_model=self._model("read"), allow_tools=["read"], max_turns=4
        )
        await asyncio.wait_for(delegate.__wrapped__(task="go"), timeout=60)
        assert ran == ["read"]

    @pytest.mark.asyncio
    async def test_parent_policy_is_not_relaxed_by_delegation(self, ran):
        """A destructive tool the parent would have gated must not be
        silently promoted to `allow` just because it was delegated."""
        delegate = make_delegate_tool(
            call_model=self._model("wipe"),
            allow_tools=["wipe"],
            policy=ToolPolicy(default="allow", destructive="deny"),
            max_turns=4,
        )
        await asyncio.wait_for(delegate.__wrapped__(task="go"), timeout=60)
        assert ran == []


async def _dispatch_content(tool_name, *, redactor=None):
    built = build_dispatch(redactor=redactor)(call=None)
    result = await asyncio.wait_for(
        Operon(built).run(inputs={"call": {"id": "1", "name": tool_name, "args": {}}}),
        timeout=30,
    )
    return result["tool_message"]["content"]
