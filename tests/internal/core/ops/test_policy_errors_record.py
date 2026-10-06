"""R1's retry and fail-fast against the structured ``"$errors"`` record (W1, C12).

A retried op fails once per invocation, not once per attempt: its record
counts invocations. ``OpFailed.error`` is the message that record holds.
"""

import pytest

from operonx import END, START, Operon, OpFailed, Retry, graph, op

FAST = dict(initial=0.01, jitter=False)
CALLS = []


@op(retry=Retry(max_attempts=3, **FAST))
async def down(x: int) -> dict:
    CALLS.append(x)
    raise ConnectionError(f"refused {x}")


@graph
def one(x):
    d = down(x=x)
    START >> d >> END


async def test_a_retried_failure_is_recorded_once():
    CALLS.clear()
    engine = Operon(one, params={"x": None})
    handle = engine.start({"x": 1})
    out = await handle.result()
    assert len(CALLS) == 3
    record = out["$errors"][f"{engine.name}.d"]
    assert record["type"] == "ConnectionError" and record["count"] == 1
    assert "refused 1" in record["message"]
    # Three attempts in the trace, the last one the failure the record names.
    nodes = [n for n in handle.trace.nodes if n.op_name == "d"]
    assert [n.attempt for n in nodes] == [1, 2, 3]
    assert nodes[-1].op_id == f"{engine.name}.d#{record['first_ctx']}"


@op
def items(n: int):
    for i in range(n):
        yield {"i": i}


@graph
def g(n):
    it = items(n=n)
    d = down(x=it["i"])
    START >> it >> d >> END


async def test_per_item_retried_failures_count_items_not_attempts():
    CALLS.clear()

    engine = Operon(g, params={"n": None})
    out = await engine.run({"n": 2})
    assert len(CALLS) == 6  # 2 items x 3 attempts
    assert out["$errors"][f"{engine.name}.d"]["count"] == 2


async def test_op_failed_carries_the_recorded_message():
    engine = Operon(one, params={"x": None}, errors="raise")
    handle = engine.start({"x": 5})
    with pytest.raises(OpFailed) as caught:
        await handle.result()
    record = handle.errors[f"{engine.name}.d"]
    assert caught.value.error == record["message"]
    assert isinstance(caught.value.__cause__, ConnectionError)
    assert "refused 5" in str(caught.value)
