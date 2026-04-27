"""Tests for PromptOp functionality."""

import pytest
from operonx.core.states import MemoryState, StateSchema


class TestPromptOpUnified:
    """Tests for PromptOp with unified prompt parameter."""

    def test_import(self):
        """Test PromptOp can be imported."""
        from operonx.providers.ops import PromptOp

        assert PromptOp is not None

    def test_string_prompt_creation(self):
        """Test creating PromptOp with string prompt (user message only)."""
        from operonx.providers.ops import PromptOp

        node = PromptOp(
            name="string_prompt",
            inputs={
                "template": "Hello {name}, help me with {task}.",
                "name": "Alice",
                "task": "coding",
            },
        )

        assert node.name == "string_prompt"
        assert node.type == "prompt"
        assert "name" in node.inputs
        assert "task" in node.inputs

    def test_dict_prompt_creation(self):
        """Test creating PromptOp with dict prompt (system/user)."""
        from operonx.providers.ops import PromptOp

        node = PromptOp(
            name="dict_prompt",
            inputs={
                "template": {"system": "You are {role}.", "user": "Help me with {task}."},
                "role": "Claude",
                "task": "coding",
            },
        )

        assert node.name == "dict_prompt"
        assert "role" in node.inputs
        assert "task" in node.inputs

    def test_list_prompt_creation(self):
        """Test creating PromptOp with list prompt (full messages)."""
        from operonx.providers.ops import PromptOp

        node = PromptOp(
            name="list_prompt",
            inputs={
                "template": [
                    {"role": "system", "content": "You are {role}."},
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "Analyze: {query}"},
                            {"type": "image_url", "image_url": {"url": "{image_url}"}},
                        ],
                    },
                ],
                "role": "a vision expert",
                "query": "What is this?",
                "image_url": "https://example.com/image.png",
            },
        )

        assert node.name == "list_prompt"
        assert "query" in node.inputs
        assert "image_url" in node.inputs

    @pytest.mark.asyncio
    async def test_format_string_prompt(self):
        """Test formatting string prompt (user message only)."""
        from operonx.providers.ops import PromptOp

        node = PromptOp(
            name="format_string",
            inputs={
                "template": "Hello {name}, help me with {task}.",
                "name": "Alice",
                "task": "coding",
            },
        )

        schema = StateSchema(op=node)
        state = MemoryState(schema)

        result = {}

        async for _, result in node.run(state):
            pass
        assert "messages" in result
        messages = result["messages"]
        assert len(messages) == 1
        assert messages[0]["role"] == "user"
        assert messages[0]["content"] == "Hello Alice, help me with coding."

    @pytest.mark.asyncio
    async def test_format_dict_prompt(self):
        """Test formatting dict prompt with system/user keys."""
        from operonx.providers.ops import PromptOp

        node = PromptOp(
            name="format_dict",
            inputs={
                "template": {"system": "You are {role}.", "user": "Help with {task}."},
                "role": "Claude",
                "task": "coding",
            },
        )

        schema = StateSchema(op=node)
        state = MemoryState(schema)

        result = {}

        async for _, result in node.run(state):
            pass
        assert "messages" in result
        messages = result["messages"]
        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        assert messages[0]["content"] == "You are Claude."
        assert messages[1]["role"] == "user"
        assert messages[1]["content"] == "Help with coding."

    @pytest.mark.asyncio
    async def test_format_dict_prompt_user_only(self):
        """Test formatting dict prompt with only user key."""
        from operonx.providers.ops import PromptOp

        node = PromptOp(
            name="format_dict_user", inputs={"template": {"user": "Hello {name}!"}, "name": "Bob"}
        )

        schema = StateSchema(op=node)
        state = MemoryState(schema)

        result = {}

        async for _, result in node.run(state):
            pass
        messages = result["messages"]
        assert len(messages) == 1
        assert messages[0]["role"] == "user"
        assert messages[0]["content"] == "Hello Bob!"

    @pytest.mark.asyncio
    async def test_format_list_prompt(self):
        """Test formatting list prompt (full messages array)."""
        from operonx.providers.ops import PromptOp

        node = PromptOp(
            name="format_list",
            inputs={
                "template": [
                    {"role": "system", "content": "You are {role}."},
                    {"role": "user", "content": "Help with {task}."},
                ],
                "role": "an assistant",
                "task": "math",
            },
        )

        schema = StateSchema(op=node)
        state = MemoryState(schema)

        result = {}

        async for _, result in node.run(state):
            pass
        messages = result["messages"]
        assert len(messages) == 2
        assert messages[0]["content"] == "You are an assistant."
        assert messages[1]["content"] == "Help with math."

    @pytest.mark.asyncio
    async def test_format_list_prompt_multimodal(self):
        """Test formatting list prompt with multimodal content."""
        from operonx.providers.ops import PromptOp

        node = PromptOp(
            name="format_multimodal",
            inputs={
                "template": [
                    {"role": "system", "content": "You are a vision expert."},
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "Describe: {query}"},
                            {"type": "image_url", "image_url": {"url": "{image_url}"}},
                        ],
                    },
                ],
                "query": "this image",
                "image_url": "https://example.com/cat.jpg",
            },
        )

        schema = StateSchema(op=node)
        state = MemoryState(schema)

        result = {}

        async for _, result in node.run(state):
            pass
        messages = result["messages"]
        assert len(messages) == 2
        assert messages[1]["content"][0]["text"] == "Describe: this image"
        assert messages[1]["content"][1]["image_url"]["url"] == "https://example.com/cat.jpg"

    @pytest.mark.asyncio
    async def test_prompt_with_conversation_history(self):
        """Test prompt with conversation history injection."""
        from operonx.providers.ops import PromptOp

        node = PromptOp(
            name="history_test",
            inputs={
                "template": {"system": "You are helpful.", "user": "Continue: {message}"},
                "message": "What was my question?",
                "conversation_history": [
                    {"role": "user", "content": "Hello"},
                    {"role": "assistant", "content": "Hi there!"},
                ],
            },
        )

        schema = StateSchema(op=node)
        state = MemoryState(schema)

        result = {}

        async for _, result in node.run(state):
            pass
        messages = result["messages"]

        # Should have: system, history (2 messages), user
        assert len(messages) == 4
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"
        assert messages[1]["content"] == "Hello"
        assert messages[2]["role"] == "assistant"
        assert messages[3]["role"] == "user"
        assert messages[3]["content"] == "Continue: What was my question?"

    @pytest.mark.asyncio
    async def test_prompt_with_tool_results(self):
        """Test prompt with tool results appended."""
        from operonx.providers.ops import PromptOp

        node = PromptOp(
            name="tool_test",
            inputs={
                "template": {"user": "What's the weather?"},
                "tool_results": [
                    {"role": "tool", "content": "Weather: Sunny, 25C", "tool_call_id": "call_123"}
                ],
            },
        )

        schema = StateSchema(op=node)
        state = MemoryState(schema)

        result = {}

        async for _, result in node.run(state):
            pass
        messages = result["messages"]

        assert len(messages) == 2
        assert messages[0]["role"] == "user"
        assert messages[1]["role"] == "tool"
        assert messages[1]["tool_call_id"] == "call_123"

    def test_metadata_with_prompt(self):
        """Test specific_metadata returns prompt info."""
        from operonx.providers.ops import PromptOp

        node = PromptOp(
            name="metadata_test",
            inputs={"template": {"system": "System prompt", "user": "User prompt"}},
        )

        metadata = node.specific_metadata
        assert "template" in metadata
        assert metadata["template"]["system"] == "System prompt"
        assert metadata["template"]["user"] == "User prompt"


class TestPromptOpSchema:
    """Tests for PromptOp schema."""

    def test_fixed_schema(self):
        """Test that PromptOp has fixed input schema."""
        from operonx.providers.ops import PromptOp

        node = PromptOp(name="schema_test", inputs={"template": "Test"})

        assert "template" in node.inputs
        assert "conversation_history" in node.inputs
        assert "tool_results" in node.inputs

    def test_output_schema(self):
        """Test that output schema has 'messages' key."""
        from operonx.providers.ops import PromptOp

        node = PromptOp(name="output_test", inputs={"template": "Test"})

        assert "messages" in node.outputs


class TestPromptOpWithVars:
    """Tests for PromptOp with template variables."""

    @pytest.mark.asyncio
    async def test_vars_formatting(self):
        """Test that template vars are used for formatting."""
        from operonx.providers.ops import PromptOp

        node = PromptOp(
            name="vars_test",
            inputs={
                "template": "Hello {name}, teach me about {topic}.",
                "name": "Alice",
                "topic": "Python",
            },
        )

        schema = StateSchema(op=node)
        state = MemoryState(schema)

        result = {}

        async for _, result in node.run(state):
            pass
        messages = result["messages"]
        assert len(messages) == 1
        assert messages[0]["content"] == "Hello Alice, teach me about Python."

    @pytest.mark.asyncio
    async def test_vars_from_state(self):
        """Test vars passed via state override defaults."""
        from operonx.providers.ops import PromptOp

        # Declare variables in inputs (with defaults) to add them to schema
        node = PromptOp(
            name="vars_state_test",
            inputs={
                "template": {"system": "You are {role}.", "user": "Task: {task}"},
                "role": "default role",  # placeholder, will be overridden
                "task": "default task",  # placeholder, will be overridden
            },
        )

        schema = StateSchema(op=node)
        # Override via state inputs
        state = MemoryState(schema, inputs={"role": "a helpful assistant", "task": "explain code"})

        result = {}

        async for _, result in node.run(state):
            pass
        messages = result["messages"]
        assert len(messages) == 2
        assert messages[0]["content"] == "You are a helpful assistant."
        assert messages[1]["content"] == "Task: explain code"

    @pytest.mark.asyncio
    async def test_empty_vars(self):
        """Test with no template variables (empty vars)."""
        from operonx.providers.ops import PromptOp

        node = PromptOp(
            name="no_vars_test",
            inputs={"template": {"system": "You are helpful.", "user": "Hello!"}},
        )

        schema = StateSchema(op=node)
        state = MemoryState(schema)

        result = {}

        async for _, result in node.run(state):
            pass
        messages = result["messages"]
        assert len(messages) == 2
        assert messages[0]["content"] == "You are helpful."
        assert messages[1]["content"] == "Hello!"


class TestPromptOpDynamic:
    """Tests for dynamic template from state."""

    @pytest.mark.asyncio
    async def test_dynamic_template_from_state(self):
        """Test receiving template dynamically from state."""
        from operonx.providers.ops import PromptOp

        node = PromptOp(name="dynamic_prompt", inputs={"template": None, "name": "default"})

        schema = StateSchema(op=node)
        state = MemoryState(
            schema,
            inputs={
                "template": {"system": "You are {name}.", "user": "Hello!"},
                "name": "a dynamic assistant",
            },
        )

        result = {}

        async for _, result in node.run(state):
            pass
        messages = result["messages"]
        assert len(messages) == 2
        assert messages[0]["content"] == "You are a dynamic assistant."
        assert messages[1]["content"] == "Hello!"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
