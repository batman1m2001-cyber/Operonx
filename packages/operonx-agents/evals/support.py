"""The system under test of the A5 eval: a support agent behind
``agent_service``, on a model that answers by rule.

The rule model is what makes the eval runnable anywhere and the same every
run: it reads the last message, asks for the tools the request names, and
answers with what the tools said. What the eval measures is the framework
around it — dispatch, approvals, the service graph, the trace the
trajectory evaluators read — not a model's judgement.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List

from operonx.app import http

from operonx_agents import Agent, InMemoryStateStore, Model, UsageLimits, agent_service, tool
from tests.agents import asks, says

ORDERS = {
    "A1": "shipped",
    "A2": "processing",
    "A3": "delivered",
    "A4": "shipped",
    "A5": "returned",
    "A6": "processing",
}


@tool(readonly=True)
async def order_status(order_id: str) -> str:
    """The shipping status of an order."""
    status = ORDERS.get(order_id)
    if status is None:
        raise LookupError(f"no order {order_id}")
    return f"Order {order_id}: {status}."


@tool(idempotent=False, approval=lambda ctx, args: args["amount"] > 500)
async def refund(order_id: str, amount: int) -> str:
    """Refund an order (over 500 needs a human)."""
    return f"Refunded {amount} on {order_id}."


@tool(idempotent=False)
async def cancel_order(order_id: str) -> str:
    """Cancel an order that has not shipped."""
    return f"Cancelled {order_id}."


_ID = re.compile(r"\bA\d\b")


def by_rule(messages: List[Dict[str, Any]], params: Dict[str, Any]) -> Any:
    """The rule model: tool results become the answer; a request becomes
    the calls it names."""
    last = messages[-1]
    if last["role"] == "tool":
        said = [m["content"] for m in messages[_last_user(messages) :] if m["role"] == "tool"]
        return says(" ".join(said))
    text = str(last["content"]).lower()
    ids = _ID.findall(str(last["content"]))
    if text.startswith("refund"):
        amount = int(re.search(r"refund (\d+)", text).group(1))
        return asks(("refund", {"order_id": ids[0], "amount": amount}))
    if text.startswith("cancel"):
        return asks(("cancel_order", {"order_id": ids[0]}))
    if ids:
        return asks(*[("order_status", {"order_id": i}) for i in ids])
    return says("How can I help with your order?")


def _last_user(messages: List[Dict[str, Any]]) -> int:
    return max(i for i, m in enumerate(messages) if m["role"] == "user")


support = Agent(
    name="support",
    model=Model("support_model"),
    instructions="You answer questions about orders. Use the tools.",
    tools=[order_status, refund, cancel_order],
    limits=UsageLimits(turns=4, tool_calls=6),
)

SERVICE = agent_service(support, http("POST", "/support"), store=InMemoryStateStore())
