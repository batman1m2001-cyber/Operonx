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


class TestPromptListForm:
    """Prompt = list of message dicts (raw OpenAI format)."""

    def test_construction(self):
        node = _make_llmop(
            prompt=[
                {"role": "system", "content": "You are {role}."},
                {"role": "user", "content": "Help with {task}."},
            ],
            role="an assistant",
            task="math",
        )
        assert "role" in node.inputs
        assert "task" in node.inputs

    def test_format(self):
        node = _make_llmop(prompt=[])
        messages = node._build_messages(
            [
                {"role": "system", "content": "You are {role}."},
                {"role": "user", "content": "Help with {task}."},
            ],
            {"role": "an assistant", "task": "math"},
        )
        assert len(messages) == 2
        assert messages[0]["content"] == "You are an assistant."
        assert messages[1]["content"] == "Help with math."

    def test_format_multimodal(self):
        node = _make_llmop(prompt=[])
        messages = node._build_messages(
            [
                {"role": "system", "content": "You are a vision expert."},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Describe: {query}"},
                        {"type": "image_url", "image_url": {"url": "{image_url}"}},
                    ],
                },
            ],
            {"query": "this image", "image_url": "https://example.com/cat.jpg"},
        )
        assert messages[1]["content"][0]["text"] == "Describe: this image"
        assert messages[1]["content"][1]["image_url"]["url"] == "https://example.com/cat.jpg"


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
