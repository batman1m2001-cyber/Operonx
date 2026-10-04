"""``"$errors"`` says what failed, how often, and where first (C12, F10/F19/F27).

Each entry is ``{type, message, count, first_ctx}``. Before, the value was
the first failure's text and nothing else: two items failing in a stream
read as one, the text was a traceback that began in operonx's own frames,
and a structured ``LLMOp`` step that failed returned ``error`` without the
run recording it — so the next templated step's ``PromptError`` was the
only failure anybody saw.
"""

import asyncio
import json
from unittest.mock import patch

from operonx.core import END, PARENT, START, GraphOp, Operon, op
from operonx.core.workflow_trace import STATUS_ERROR, format_ctx
from operonx.telemetry.consumers.local import LocalConsumer


@op
def each(orders: list):
    for order in orders:
        yield {"order": order}


@op
def enrich(order: dict):
    if order["amount"] < 0:
        raise ValueError(f"order {order['id']} has a negative amount")
    return {"row": order["id"]}


def _pipeline():
    with GraphOp(name="pipe") as g:
        e = each(orders=PARENT["orders"])
        en = enrich(order=e["order"])
        START >> e >> en >> END
    return g


ORDERS = [{"id": i, "amount": 10} for i in range(3)] + [
    {"id": 100, "amount": -1},
    {"id": 200, "amount": -2},
]


class TestStructuredEntry:
    async def test_two_failing_items_are_counted_not_merged(self):
        """`probe_errors.py`: orders 100 and 200 both raise."""
        engine = Operon(_pipeline())
        handle = engine.start(inputs={"orders": ORDERS})
        out = await handle.result()

        entry = out["$errors"][f"{engine.name}.en"]
        assert set(entry) == {"type", "message", "count", "first_ctx"}
        assert entry["type"] == "ValueError"
        assert entry["count"] == 2
        assert "order 100 has a negative amount" in entry["message"]

        # first_ctx names the failed execution in the trace: op_id is
        # "<full name>#<first_ctx>".
        failed = [n for n in handle.trace.nodes if n.status == STATUS_ERROR]
        assert [format_ctx(n.ctx) for n in failed][0] == entry["first_ctx"]
        assert f"{engine.name}.en#{entry['first_ctx']}" == failed[0].op_id

    async def test_message_keeps_user_frames_and_trace_keeps_the_rest(self):
        engine = Operon(_pipeline())
        handle = engine.start(inputs={"orders": ORDERS})
        out = await handle.result()

        message = out["$errors"][f"{engine.name}.en"]["message"]
        assert message.startswith("Traceback (most recent call last):")
        assert __file__ in message  # the frame that raised
        assert "/operonx/core/" not in message
        assert message.rstrip().endswith("ValueError: order 100 has a negative amount")

        # The trace node keeps the traceback whole, operonx frames included.
        node = next(n for n in handle.trace.nodes if n.status == STATUS_ERROR)
        assert "/operonx/core/ops/base.py" in node.error

    async def test_an_error_raised_inside_operonx_is_its_last_line(self):
        """No user frame at all: the message is the exception line alone."""
        with GraphOp(name="g") as g:
            # A missing required input fails in operonx's own code.
            e = enrich(order=PARENT["order"])
            START >> e >> END
        out = await Operon(g).run(inputs={"order": None})
        entry = out["$errors"]["g.e"]
        assert entry["type"] == "TypeError"
        assert entry["message"].startswith("TypeError: ")
        assert "\n" not in entry["message"].strip()

    async def test_a_subgraph_entry_has_a_type_and_its_ctx(self):
        with GraphOp(name="outer") as g:
            with GraphOp(name="inner") as sub:
                p = enrich(order=PARENT["order"])
                START >> p >> END
            START >> sub >> END
        out = await Operon(g).run(inputs={"order": {"id": 1, "amount": -1}})
        entry = out["$errors"]["outer.inner"]
        assert entry["type"] == "SubgraphError"
        assert "'outer.inner.p' raised" in entry["message"]
        assert entry["count"] == 1
        assert entry["first_ctx"] == "main"

    async def test_the_error_cell_keeps_the_full_text(self):
        """The op's own `error` cell is unchanged: per context, full text."""
        with GraphOp(name="g") as g:
            p = enrich(order=PARENT["order"])
            START >> p >> END
        out = await Operon(g).run(inputs={"order": {"id": 1, "amount": -1}})
        cell = out["$state"]["g.p", "error"]
        assert isinstance(cell, str) and "/operonx/core/ops/base.py" in cell
        assert out["$errors"]["g.p"]["type"] == "ValueError"


class TestStructuredLLMFailure:
    """p1 edge C: extraction fails, the next templated step raises
    `PromptError`; the cause — the parse failure — must be in `$errors`."""

    def _run(self, responses, **ex_kwargs):
        from operonx.providers.ops import LLMOp
        from tests.internal.providers.test_extract_retry import make_mock_hub

        mock_hub, _ = make_mock_hub(responses)
        with patch("operonx.providers.ops._utils.ResourceHub") as mock_cls:
            mock_cls.instance.return_value = mock_hub
            with GraphOp(name="chain") as g:
                ex = LLMOp.of(
                    resource="mock",
                    prompt={"user": "{text}"},
                    fields=["urgency: int", "summary: str"],
                    parser="json",
                    text=PARENT["text"],
                    **ex_kwargs,
                )
                dr = LLMOp.of(
                    resource="mock",
                    prompt={"user": "Urgency {urgency}: {summary}"},
                    urgency=ex["urgency"],
                    summary=ex["summary"],
                )
                START >> ex >> dr >> END
            return asyncio.run(Operon(g).run(inputs={"text": "ticket"}))

    def test_parse_failure_lands_in_errors(self):
        out = self._run(["not json at all"])
        entry = out["$errors"]["chain.ex"]
        assert entry["type"] == "ParserError"
        assert entry["message"].startswith("ParserError: Parse error (json)")
        assert entry["count"] == 1
        assert entry["first_ctx"] == "main"
        # The op's `error` output is unchanged, and is where the text came from.
        assert entry["message"] == f"ParserError: {out['$state']['chain.ex', 'error']}"

    def test_a_hard_failure_reported_as_error_lands_in_errors(self):
        from operonx.providers.ops import LLMOp

        async def boom(self, params):
            raise ConnectionError("gateway down")

        with patch.object(LLMOp, "_call_once", boom):
            out = self._run(["unused"], on_failure="error")
        entry = out["$errors"]["chain.ex"]
        assert entry["type"] == "ConnectionError"
        assert "gateway down" in entry["message"]

    def test_success_records_nothing(self):
        out = self._run(['{"urgency": 2, "summary": "printer"}', "drafted"])
        assert "$errors" not in out


class TestMetaJson:
    async def _meta(self, tmp_path, inputs):
        engine = Operon(_pipeline(), trace=LocalConsumer(config={"root": str(tmp_path)}))
        await engine.run(inputs=inputs)
        (meta_path,) = tmp_path.rglob("meta.json")
        return json.loads(meta_path.read_text())

    async def test_failed_run_has_status_and_errors(self, tmp_path):
        meta = await self._meta(tmp_path, {"orders": ORDERS})
        assert meta["status"] == "error"
        entry = meta["errors"]["pipe.en"]
        assert entry["type"] == "ValueError" and entry["count"] == 2

    async def test_clean_run_is_ok_with_no_errors(self, tmp_path):
        meta = await self._meta(tmp_path, {"orders": ORDERS[:2]})
        assert meta["status"] == "ok"
        assert meta["errors"] == {}
