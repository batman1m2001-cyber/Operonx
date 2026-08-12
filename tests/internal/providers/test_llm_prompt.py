"""Tests for LLMOp's absorbed prompt-formatting behavior."""

import pytest

from operonx.core.exceptions import PromptError
from operonx.providers.ops.llm import LLMOp


def _make_llmop(**inputs):
    """Build a minimal LLMOp for pure prompt-formatting checks."""
    return LLMOp(name="test_llm", resource="gpt-4o", inputs=inputs)


class TestPromptStringForm:
    """Prompt = str → single user message."""

    def test_construction(self):
        node = _make_llmop(prompt="Hello {name}", name="Alice")
        assert node.type == "llm"
        assert "prompt" in node.inputs
        assert "name" in node.inputs

    def test_format(self):
        node = _make_llmop(prompt="Hello {name}, help me with {task}.")
        messages = node._build_messages(
            "Hello {name}, help me with {task}.", {"name": "Alice", "task": "coding"}
        )
        assert messages == [{"role": "user", "content": "Hello Alice, help me with coding."}]

    def test_no_placeholders(self):
        node = _make_llmop(prompt="static content")
        messages = node._build_messages("static content", {})
        assert messages == [{"role": "user", "content": "static content"}]


class TestPromptDictForm:
    """Prompt = dict with system/user keys."""

    def test_construction(self):
        node = _make_llmop(
            prompt={"system": "You are {role}.", "user": "Help me with {task}."},
            role="Claude",
            task="coding",
        )
        assert "role" in node.inputs
        assert "task" in node.inputs

    def test_format_both_keys(self):
        node = _make_llmop(prompt={})
        messages = node._build_messages(
            {"system": "You are {role}.", "user": "Task: {task}"},
            {"role": "helpful", "task": "explain"},
        )
        assert messages == [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Task: explain"},
        ]

    def test_format_user_only(self):
        node = _make_llmop(prompt={})
        messages = node._build_messages({"user": "Hello {name}!"}, {"name": "Bob"})
        assert messages == [{"role": "user", "content": "Hello Bob!"}]

    def test_format_system_only(self):
        node = _make_llmop(prompt={})
        messages = node._build_messages({"system": "You are {role}."}, {"role": "helpful"})
        assert messages == [{"role": "system", "content": "You are helpful."}]

    def test_invalid_dict_no_system_user_raises(self):
        node = _make_llmop(prompt={})
        with pytest.raises(PromptError):
            node._build_messages({"role": "assistant", "content": "hi"}, {})


class TestMessagesForm:
    """``messages=`` — a conversation, passed through untouched.

    ``prompt=`` used to accept a list and format it. That made every agent
    a bug waiting for a brace: a tool returning ``{"city": "Hanoi"}``, a
    user pasting CSS, or the model's own tool-call arguments all became
    template variables that do not exist, and the run died on the *next*
    model call.
    """

    def test_a_list_prompt_is_refused_and_names_the_replacement(self):
        node = _make_llmop(prompt="stub")
        with pytest.raises(PromptError, match="messages="):
            node._build_messages([{"role": "user", "content": "hi"}], {})

    def test_messages_pass_through_unformatted(self):
        node = _make_llmop(prompt="stub")
        history = [
            {"role": "user", "content": "what is the weather"},
            {"role": "tool", "content": '{"city": "Hanoi", "temp": 30}'},
        ]
        out = node._resolve_messages(None, history, {})
        assert out == history, "a conversation must arrive exactly as written"

    @pytest.mark.parametrize(
        "content",
        [
            pytest.param('{"city": "Hanoi"}', id="json-tool-result"),
            pytest.param("body { margin: 0 }", id="pasted-css"),
            pytest.param("def f(): return {'a': 1}", id="pasted-python"),
            pytest.param("cost is {", id="unmatched-brace"),
        ],
    )
    def test_braces_that_used_to_kill_the_run(self, content):
        node = _make_llmop(prompt="stub")
        out = node._resolve_messages(None, [{"role": "tool", "content": content}], {})
        assert out[0]["content"] == content

    def test_multimodal_blocks_survive(self):
        """The case that justified list-shaped prompts. It still works — the
        substitution just happens in an upstream op now, where an f-string
        is clearer than ``{}`` magic."""
        node = _make_llmop(prompt="stub")
        history = [
            {"role": "system", "content": "You are a vision expert."},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Describe: this image"},
                    {"type": "image_url", "image_url": {"url": "https://x/cat.jpg"}},
                ],
            },
        ]
        out = node._resolve_messages(None, history, {})
        assert out[1]["content"][1]["image_url"]["url"] == "https://x/cat.jpg"

    def test_both_inputs_is_an_error(self):
        node = _make_llmop(prompt="stub")
        with pytest.raises(PromptError, match="mutually exclusive"):
            node._resolve_messages("hi", [{"role": "user", "content": "hi"}], {})

    def test_neither_input_is_an_error(self):
        node = _make_llmop(prompt="stub")
        with pytest.raises(PromptError, match="required"):
            node._resolve_messages(None, None, {})

    def test_template_vars_with_messages_raise(self):
        """Ignoring them would send the unsubstituted text to the model and
        report success — the caller asked for something that cannot happen."""
        node = _make_llmop(prompt="stub")
        with pytest.raises(PromptError, match="never formatted"):
            node._resolve_messages(None, [{"role": "user", "content": "hi"}], {"name": "x"})

    def test_messages_must_be_a_list(self):
        node = _make_llmop(prompt="stub")
        with pytest.raises(PromptError, match="must be a list"):
            node._resolve_messages(None, "not a list", {})


class TestPromptErrors:
    """Error paths in prompt formatting."""

    def test_missing_variable(self):
        node = _make_llmop(prompt="Hello {missing}")
        with pytest.raises(PromptError) as excinfo:
            node._build_messages("Hello {missing}", {})
        assert "missing" in excinfo.value.missing_vars

    def test_invalid_type(self):
        node = _make_llmop(prompt="stub")
        with pytest.raises(PromptError):
            node._build_messages(12345, {})


class TestPromptSchema:
    """LLMOp input/output schema exposed after prompt absorption."""

    def test_prompt_in_input_schema(self):
        node = _make_llmop(prompt="Test")
        assert "prompt" in node.inputs

    def test_llm_knob_defaults(self):
        node = _make_llmop(prompt="Test")
        assert "temperature" in node.inputs
        assert "max_tokens" in node.inputs
        assert "tools" in node.inputs

    def test_output_schema(self):
        node = _make_llmop(prompt="Test")
        assert "content" in node.outputs
        assert "role" in node.outputs
        assert "model_used" in node.outputs


class TestPromptMetadata:
    """specific_metadata exposes the prompt for tracing."""

    def test_prompt_in_metadata(self):
        node = _make_llmop(prompt={"system": "System msg", "user": "User msg"})
        metadata = node.specific_metadata
        assert "prompt" in metadata
        assert metadata["prompt"]["system"] == "System msg"
        assert metadata["prompt"]["user"] == "User msg"


class TestPromptViaBuildLLMParams:
    """End-to-end: _build_llm_params runs prompt formatting."""

    def test_llm_params_include_messages(self):
        node = _make_llmop(prompt="Hi {name}")
        params = node._build_llm_params({"prompt": "Hi {name}", "name": "Ada", "temperature": 0.7})
        assert params["messages"] == [{"role": "user", "content": "Hi Ada"}]
        assert params["temperature"] == 0.7

    def test_llm_params_drops_none_knobs(self):
        node = _make_llmop(prompt="Hi")
        params = node._build_llm_params({"prompt": "Hi", "temperature": None, "max_tokens": 42})
        assert "temperature" not in params
        assert params["max_tokens"] == 42

    def test_missing_prompt_raises(self):
        node = _make_llmop(prompt="stub")
        with pytest.raises(PromptError):
            node._build_llm_params({"prompt": None})


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
