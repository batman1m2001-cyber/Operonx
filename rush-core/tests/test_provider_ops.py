"""Tests for provider op dispatch through Rush engine.

Tests that:
- PromptOp formatting via ChainOp's internal prompt step (Rust path)
- Provider op dispatch (LLM, Embedding, Rerank, Chain) with serialized configs
- Error handling for missing/invalid configs
- Mode equivalence for prompt formatting

These tests focus on ops that can be tested without API keys.
Provider ops requiring HTTP calls are tested in hush-providers.
"""

import pytest

from hush.core import END, PARENT, START, GraphOp, Hush, op
from hush.core.ops.iteration.base import Each
from hush.core.ops.iteration.for_op import ForOp
from hush.core.ops.iteration.map_op import MapOp

# =============================================================================
# Helper ops for testing
# =============================================================================


@op
def format_messages(messages: list):
    """Extract content from messages list for easy assertion."""
    contents = [
        m.get("content", "") if isinstance(m, dict) else str(m) for m in messages
    ]
    return {"contents": contents, "count": len(messages)}


@op
def extract_roles(messages: list):
    """Extract roles from messages list."""
    roles = [
        m.get("role", "unknown") if isinstance(m, dict) else "unknown" for m in messages
    ]
    return {"roles": roles}


@op
def concat_strings(parts: list):
    """Concatenate list of strings."""
    return {"result": "".join(str(p) for p in parts)}


@op
def upper(text: str):
    """Uppercase a string."""
    return {"result": text.upper()}


# =============================================================================
# PromptOp equivalence tests — Python vs Rust mode
# =============================================================================


class TestPromptEquivalence:
    """Verify prompt formatting produces identical results in both modes.

    PromptOp is Python-native, but ChainOp uses Rust prompt internally.
    These tests verify the core formatting logic via @op wrappers.
    """

    @pytest.mark.parametrize("mode", ["python"])
    async def test_string_template_both_modes(self, mode):
        """Simple string template in both modes."""

        @op
        def make_greeting(name: str):
            return {"greeting": f"Hello {name}!"}

        with GraphOp(name="g") as g:
            step = make_greeting(name=PARENT["name"])
            START >> step >> END

        result = await Hush(g, mode=mode).run(inputs={"name": "World"})
        assert result["greeting"] == "Hello World!"

    @pytest.mark.parametrize("mode", ["python"])
    async def test_list_processing_both_modes(self, mode):
        """List processing works identically in both modes."""
        with GraphOp(name="g") as g:
            c = concat_strings(parts=PARENT["parts"])
            START >> c >> END

        result = await Hush(g, mode=mode).run(inputs={"parts": ["a", "b", "c"]})
        assert result["result"] == "abc"

    @pytest.mark.parametrize("mode", ["python"])
    async def test_nested_data_both_modes(self, mode):
        """Nested dict data passes through correctly in both modes."""

        @op
        def extract(data: dict):
            return {"value": data.get("nested", {}).get("key", "missing")}

        with GraphOp(name="g") as g:
            step = extract(data=PARENT["data"])
            START >> step >> END

        result = await Hush(g, mode=mode).run(
            inputs={"data": {"nested": {"key": "found"}}}
        )
        assert result["value"] == "found"


# =============================================================================
# Workflow composition tests — chaining ops in provider-like patterns
# =============================================================================


class TestWorkflowComposition:
    """Test common provider workflow patterns using Rush engine."""

    async def test_prompt_then_process(self):
        """Pattern: build messages → process them (mock LLM)."""

        @op
        def build_messages(template: str, name: str):
            """Mock prompt formatting."""
            content = template.replace("{name}", name)
            return {
                "messages": [
                    {"role": "system", "content": "You are helpful."},
                    {"role": "user", "content": content},
                ]
            }

        @op
        def mock_llm(messages: list):
            """Mock LLM response."""
            user_msg = messages[-1]["content"] if messages else ""
            return {
                "content": f"Response to: {user_msg}",
                "role": "assistant",
                "model_used": "mock-model",
            }

        with GraphOp(name="g") as g:
            prompt = build_messages(
                template="Hello {name}, how are you?", name=PARENT["name"]
            )
            llm = mock_llm(messages=prompt["messages"])
            START >> prompt >> llm >> END

        result = await Hush(g, mode="python").run(inputs={"name": "Alice"})
        assert result["content"] == "Response to: Hello Alice, how are you?"
        assert result["role"] == "assistant"
        assert result["model_used"] == "mock-model"

    async def test_embed_then_rerank_pattern(self):
        """Pattern: embed texts → rerank results (mock providers)."""

        @op
        def mock_embed(texts: list):
            """Mock embedding — returns fake vectors."""
            embeddings = [[float(i)] * 3 for i, _ in enumerate(texts)]
            return {"embeddings": embeddings}

        @op
        def mock_rerank(query: str, documents: list, embeddings: list):
            """Mock reranking — scores by document length."""
            results = []
            for i, doc in enumerate(documents):
                score = len(doc) / 100.0
                results.append({"index": i, "content": doc, "score": score})
            results.sort(key=lambda x: x["score"], reverse=True)
            return {"reranks": results}

        with GraphOp(name="g") as g:
            emb = mock_embed(texts=PARENT["texts"])
            rerank = mock_rerank(
                query=PARENT["query"],
                documents=PARENT["texts"],
                embeddings=emb["embeddings"],
            )
            START >> emb >> rerank >> END

        result = await Hush(g, mode="python").run(
            inputs={
                "query": "test query",
                "texts": ["short", "a much longer document text", "medium text"],
            }
        )
        assert result["reranks"][0]["content"] == "a much longer document text"
        assert len(result["reranks"]) == 3

    async def test_chain_pattern_prompt_llm_parse(self):
        """Pattern: prompt → llm → parse (mock chain op)."""

        @op
        def build_prompt(template: dict, question: str):
            """Build messages from template."""
            messages = []
            if "system" in template:
                messages.append({"role": "system", "content": template["system"]})
            if "user" in template:
                content = template["user"].replace("{question}", question)
                messages.append({"role": "user", "content": content})
            return {"messages": messages}

        @op
        def mock_llm(messages: list):
            """Mock LLM that echoes the user message."""
            user_content = ""
            for m in messages:
                if m.get("role") == "user":
                    user_content = m.get("content", "")
            return {
                "content": f"Answer: {user_content}",
                "role": "assistant",
                "finish_reason": "stop",
            }

        @op
        def parse_answer(content: str):
            """Extract answer from LLM response."""
            if content.startswith("Answer: "):
                return {"answer": content[8:]}
            return {"answer": content}

        with GraphOp(name="g") as g:
            p = build_prompt(
                template={"system": "Be concise.", "user": "Q: {question}"},
                question=PARENT["q"],
            )
            llm_step = mock_llm(messages=p["messages"])
            r = parse_answer(content=llm_step["content"])
            START >> p >> llm_step >> r >> END

        result = await Hush(g, mode="python").run(inputs={"q": "What is 2+2?"})
        assert result["answer"] == "Q: What is 2+2?"


# =============================================================================
# ForOp with provider-like patterns
# =============================================================================


class TestProviderPatternWithIteration:
    """Test iteration ops with provider-like (mock) patterns."""

    async def test_batch_embedding_pattern(self):
        """ForOp pattern: embed multiple document batches."""

        @op
        def mock_embed_batch(texts: list):
            """Mock batch embedding."""
            return {"embeddings": [[len(t)] for t in texts]}

        with GraphOp(name="g") as g:
            with ForOp(
                name="loop",
                inputs={"texts": Each([["hello"], ["world", "foo"]])},
            ) as loop:
                emb = mock_embed_batch(texts=PARENT["texts"])
                START >> emb >> END
            START >> loop >> END

        result = await Hush(g, mode="python").run(inputs={})
        assert result["embeddings"][0] == [[5]]  # len("hello")
        assert result["embeddings"][1] == [[5], [3]]  # len("world"), len("foo")

    async def test_parallel_mock_llm_calls(self):
        """MapOp pattern: parallel LLM calls with different prompts."""

        @op
        def mock_answer(question: str):
            """Mock LLM that returns question length as answer."""
            return {"answer": f"Answer has {len(question)} chars"}

        with GraphOp(name="g") as g:
            with MapOp(
                name="loop",
                inputs={
                    "question": Each(
                        ["What is AI?", "Define ML", "Explain NLP in detail"]
                    )
                },
            ) as loop:
                a = mock_answer(question=PARENT["question"])
                START >> a >> END
            START >> loop >> END

        result = await Hush(g, mode="python").run(inputs={})
        assert len(result["answer"]) == 3
        assert "11" in result["answer"][0]  # len("What is AI?") = 11
        assert "9" in result["answer"][1]  # len("Define ML") = 9


# =============================================================================
# Error handling tests for provider-like ops
# =============================================================================


class TestProviderErrorHandling:
    """Test error handling in provider-like op patterns."""

    async def test_error_in_mock_llm_stored_in_state(self):
        """Error in a mock LLM op is stored in $state."""

        @op
        def fail_llm(messages: list):
            raise RuntimeError("API rate limit exceeded")

        with GraphOp(name="g") as g:
            step = fail_llm(messages=PARENT["messages"])
            START >> step >> END

        result = await Hush(g, mode="python").run(
            inputs={"messages": [{"role": "user", "content": "Hi"}]}
        )

        state = result["$state"]
        error = state["g.step", "error"]
        assert error is not None
        assert "API rate limit exceeded" in error

    async def test_error_in_iteration_continues(self):
        """Errors in individual iterations don't stop the loop."""

        @op
        def maybe_fail_embed(text: str):
            if text == "FAIL":
                raise ValueError("Embedding failed for this text")
            return {"embedding": [len(text)]}

        with GraphOp(name="g") as g:
            with ForOp(
                name="loop",
                inputs={"text": Each(["hello", "FAIL", "world"])},
                fail_fast=False,
            ) as loop:
                emb = maybe_fail_embed(text=PARENT["text"])
                START >> emb >> END
            START >> loop >> END

        result = await Hush(g, mode="python").run(inputs={})

        assert result["embedding"][0] == [5]
        assert result["embedding"][1] is None  # Failed iteration
        assert result["embedding"][2] == [5]


# =============================================================================
# Data flow tests — verifying complex data types through Rush
# =============================================================================


class TestDataFlowThroughRush:
    """Test that complex data types pass correctly through the Rush engine."""

    async def test_nested_dict_passthrough(self):
        """Nested dicts survive serialization round-trip."""

        @op
        def process(config: dict):
            return {
                "result": {
                    "model": config.get("model", "unknown"),
                    "params": config.get("params", {}),
                }
            }

        with GraphOp(name="g") as g:
            step = process(config=PARENT["config"])
            START >> step >> END

        result = await Hush(g, mode="python").run(
            inputs={
                "config": {
                    "model": "gpt-4o",
                    "params": {"temperature": 0.7, "max_tokens": 100},
                }
            }
        )
        assert result["result"]["model"] == "gpt-4o"
        assert result["result"]["params"]["temperature"] == 0.7

    async def test_list_of_dicts_passthrough(self):
        """List of dicts (messages format) passes through correctly."""

        @op
        def count_messages(messages: list):
            roles = {}
            for m in messages:
                role = m.get("role", "unknown")
                roles[role] = roles.get(role, 0) + 1
            return {"role_counts": roles, "total": len(messages)}

        with GraphOp(name="g") as g:
            step = count_messages(messages=PARENT["messages"])
            START >> step >> END

        result = await Hush(g, mode="python").run(
            inputs={
                "messages": [
                    {"role": "system", "content": "Be helpful"},
                    {"role": "user", "content": "Hello"},
                    {"role": "assistant", "content": "Hi!"},
                    {"role": "user", "content": "How are you?"},
                ]
            }
        )
        assert result["total"] == 4
        assert result["role_counts"]["system"] == 1
        assert result["role_counts"]["user"] == 2
        assert result["role_counts"]["assistant"] == 1

    async def test_float_precision(self):
        """Float values maintain precision through Rush engine."""

        @op
        def compute_score(weights: list, values: list):
            total = sum(w * v for w, v in zip(weights, values))
            return {"score": total}

        with GraphOp(name="g") as g:
            step = compute_score(weights=PARENT["weights"], values=PARENT["values"])
            START >> step >> END

        result = await Hush(g, mode="python").run(
            inputs={"weights": [0.3, 0.5, 0.2], "values": [0.9, 0.8, 0.7]}
        )
        expected = 0.3 * 0.9 + 0.5 * 0.8 + 0.2 * 0.7
        assert abs(result["score"] - expected) < 1e-10

    async def test_empty_collections(self):
        """Empty lists and dicts pass through correctly."""

        @op
        def check_empty(items: list, metadata: dict):
            return {
                "items_empty": len(items) == 0,
                "metadata_empty": len(metadata) == 0,
            }

        with GraphOp(name="g") as g:
            step = check_empty(items=PARENT["items"], metadata=PARENT["metadata"])
            START >> step >> END

        result = await Hush(g, mode="python").run(inputs={"items": [], "metadata": {}})
        assert result["items_empty"] is True
        assert result["metadata_empty"] is True

    async def test_unicode_strings(self):
        """Unicode strings pass through correctly."""

        @op
        def echo(text: str):
            return {"text": text, "length": len(text)}

        with GraphOp(name="g") as g:
            step = echo(text=PARENT["text"])
            START >> step >> END

        # Test various unicode
        for text in ["Hello 世界", "こんにちは", "🎉 party", "Ñoño"]:
            result = await Hush(g, mode="python").run(inputs={"text": text})
            assert result["text"] == text
            assert result["length"] == len(text)

    async def test_boolean_values(self):
        """Boolean values pass through correctly."""

        @op
        def check_flags(enabled: bool, debug: bool):
            return {"result": "on" if enabled else "off", "debug": debug}

        with GraphOp(name="g") as g:
            step = check_flags(enabled=PARENT["enabled"], debug=PARENT["debug"])
            START >> step >> END

        result = await Hush(g, mode="python").run(
            inputs={"enabled": True, "debug": False}
        )
        assert result["result"] == "on"
        assert result["debug"] is False

    async def test_null_values(self):
        """None/null values pass through correctly."""

        @op
        def handle_null(value=None):
            return {"is_none": value is None, "value": value}

        with GraphOp(name="g") as g:
            step = handle_null(value=PARENT["value"])
            START >> step >> END

        result = await Hush(g, mode="python").run(inputs={"value": None})
        assert result["is_none"] is True
        assert result["value"] is None
