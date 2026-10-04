"""An LLM execution's trace records the request it sent (C15).

The trace kept the template and the variables; the messages the model
received existed only inside the op. ``LLMOp.normalize_trace_io`` renders
them — and wraps multimodal blocks as ``Media`` — but nothing called it
(``_extract_trace_io`` had no caller), so Studio had to re-implement the
rendering to show a prompt.
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from operonx.core import END, PARENT, START, GraphOp, Media, Operon
from operonx.core.workflow_trace import STATUS_ERROR
from tests.internal.providers.test_extract_retry import make_mock_hub

pytestmark = pytest.mark.unit

TEMPLATE = {"system": "You triage tickets.", "user": "Ticket: {text}"}
SENT = [
    {"role": "system", "content": "You triage tickets."},
    {"role": "user", "content": "Ticket: printer on fire"},
]


async def _trace_of(responses=("ok",), **llm_kwargs):
    from operonx.providers.ops import LLMOp

    mock_hub, calls = make_mock_hub(list(responses))

    async def stream(messages, **kwargs):
        calls["messages_history"].append(list(messages))
        for piece in ("o", "k", None):
            delta = SimpleNamespace(
                content=piece, tool_calls=None, reasoning_content=None, refusal=None
            )
            choice = SimpleNamespace(delta=delta, finish_reason=None if piece else "stop")
            yield SimpleNamespace(choices=[choice], usage=None)

    mock_hub.get.return_value.stream = stream
    with patch("operonx.providers.ops._utils.ResourceHub") as mock_cls:
        mock_cls.instance.return_value = mock_hub
        with GraphOp(name="g") as g:
            llm = LLMOp.of(resource="mock", **llm_kwargs)
            START >> llm >> END
        handle = Operon(g).start(inputs={"text": "printer on fire"})
        await handle.result()
    nodes = [n for n in handle.trace.nodes if n.op_name == "llm"]
    return nodes, calls


async def test_the_rendered_messages_are_recorded_beside_the_variables():
    (node,), calls = await _trace_of(prompt=TEMPLATE, text=PARENT["text"])
    assert node.inputs["messages"] == SENT
    assert node.inputs["messages"] == calls["messages_history"][0]  # what was sent
    assert node.inputs["text"] == "printer on fire"  # the variables stay
    assert node.inputs["prompt"] == TEMPLATE  # and so does the template


async def test_a_conversation_passed_as_messages_is_recorded_once():
    convo = [{"role": "user", "content": "hi {not a template}"}]
    (node,), _ = await _trace_of(messages=convo)
    assert node.inputs["messages"] == convo
    assert [k for k in node.inputs if k == "messages"] == ["messages"]


async def test_a_variable_hidden_from_the_trace_is_not_leaked_through_messages():
    (node,), _ = await _trace_of(prompt=TEMPLATE, text=PARENT["text"], exclude={"trace": ["text"]})
    assert "text" not in node.inputs
    assert "messages" not in node.inputs
    assert "printer on fire" not in repr(node.inputs)


async def test_messages_can_be_hidden_on_their_own():
    (node,), _ = await _trace_of(
        prompt=TEMPLATE, text=PARENT["text"], exclude={"trace": ["messages"]}
    )
    assert "messages" not in node.inputs
    assert node.inputs["text"] == "printer on fire"


async def test_an_include_list_without_messages_records_none():
    (node,), _ = await _trace_of(prompt=TEMPLATE, text=PARENT["text"], include=["text"])
    assert set(node.inputs) == {"text"}


async def test_a_prompt_that_does_not_render_fails_the_op_not_the_trace():
    (node,), _ = await _trace_of(prompt={"user": "Ticket: {missing}"}, text=PARENT["text"])
    assert node.status == STATUS_ERROR
    assert "PromptError" in node.error
    assert "messages" not in node.inputs


async def test_streaming_renders_the_request_once_for_every_frame():
    nodes, _ = await _trace_of(prompt=TEMPLATE, text=PARENT["text"], stream=True)
    assert len(nodes) >= 2  # one record per yielded frame
    first = nodes[0].inputs["messages"]
    assert first == SENT
    assert all(n.inputs["messages"] is first for n in nodes)


async def test_an_image_block_is_media_in_the_trace():
    convo = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "What is this?"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,iVBORw0KGgo="}},
            ],
        }
    ]
    (node,), _ = await _trace_of(messages=convo)
    url = node.inputs["messages"][0]["content"][1]["image_url"]["url"]
    assert isinstance(url, Media) and url.mime_type == "image/png"
    assert isinstance(convo[0]["content"][1]["image_url"]["url"], str)  # the request is untouched


async def test_a_hidden_template_is_not_rendered_into_the_trace():
    (node,), _ = await _trace_of(
        prompt=TEMPLATE, text=PARENT["text"], exclude={"trace": ["prompt"]}
    )
    assert "prompt" not in node.inputs
    assert "messages" not in node.inputs
