"""Tests for LLMOp structured-output mode (the surface that replaced ask()).

The old ``ask()`` helper was removed in 1.0.0; its behaviour lives in
``LLMOp(fields=..., parser=..., validators=..., max_retries=..., retry_hint=...)``.
This suite locks in the construction-time contracts of that surface —
runtime behaviour is exercised in ``test_extract_retry.py``.
"""

from unittest.mock import AsyncMock, Mock, patch

import pytest


def _mock_resource_hub():
    mock_hub = Mock()
    mock_hub.llm.return_value = Mock(generate=AsyncMock(), stream=AsyncMock())
    mock_hub.get.return_value = Mock(generate=AsyncMock(), stream=AsyncMock())
    return mock_hub


class TestLLMStructuredConstruction:
    def test_fields_creates_typed_outputs(self):
        from operonx.providers.ops import LLMOp

        with patch("operonx.providers.ops._utils.ResourceHub") as mock_cls:
            mock_cls.instance.return_value = _mock_resource_hub()
            node = LLMOp.of(
                resource="gpt-4",
                prompt="Classify: {text}",
                fields=["category: str", "confidence: float"],
                parser="xml",
                text="sample",
            )

        assert "category" in node.outputs
        assert "confidence" in node.outputs
        assert "error" in node.outputs
        assert node.parser == "xml"
        assert node.max_retries == 0

    def test_parser_defaults_to_xml_when_only_fields_given(self):
        from operonx.providers.ops import LLMOp

        with patch("operonx.providers.ops._utils.ResourceHub") as mock_cls:
            mock_cls.instance.return_value = _mock_resource_hub()
            node = LLMOp.of(
                resource="gpt-4",
                prompt="Classify: {text}",
                fields=["result: str"],
                text="sample",
            )
        assert node.parser == "xml"

    def test_parser_without_fields_raises(self):
        from operonx.providers.ops import LLMOp

        with pytest.raises(TypeError, match="fields"):
            LLMOp.of(resource="gpt-4", prompt="X", parser="xml")

    def test_validators_without_fields_raises(self):
        from operonx.providers.ops import LLMOp

        with pytest.raises(TypeError, match="fields"):
            LLMOp.of(
                resource="gpt-4",
                prompt="X",
                validators={"a": ["yes"]},
            )

    def test_negative_max_retries_raises(self):
        from operonx.providers.ops import LLMOp

        with pytest.raises(ValueError, match="max_retries"):
            LLMOp.of(
                resource="gpt-4",
                prompt="X",
                fields=["a: str"],
                max_retries=-1,
            )

    def test_simple_mode_has_no_error_output(self):
        """Without fields=, LLMOp keeps its original output schema —
        no extracted-field outputs, no ``error`` output. Migrating a
        raw-content caller stays a no-op."""
        from operonx.providers.ops import LLMOp

        with patch("operonx.providers.ops._utils.ResourceHub") as mock_cls:
            mock_cls.instance.return_value = _mock_resource_hub()
            node = LLMOp.of(
                resource="gpt-4",
                prompt="Summarize: {text}",
                text="sample",
            )
        assert "error" not in node.outputs
        assert "content" in node.outputs

    def test_extract_fields_are_dataclass_instances(self):
        from operonx.providers.ops import LLMOp
        from operonx.providers.parsing import ExtractField

        with patch("operonx.providers.ops._utils.ResourceHub") as mock_cls:
            mock_cls.instance.return_value = _mock_resource_hub()
            node = LLMOp.of(
                resource="gpt-4",
                prompt="X: {text}",
                fields=["user.name: str", "user.age: int"],
                text="s",
            )
        assert all(isinstance(f, ExtractField) for f in node._extract_fields)
        assert node._extract_fields[0].chain_path == ["user", "name"]
        assert node._extract_fields[0].output_key == "name"
        assert node._extract_fields[1].type_hint == "int"


class TestLLMStructuredInsideGraph:
    def test_ref_prompt_still_infers_template_vars(self):
        """LLMOp inside a @graph with prompt=PARENT[...] still picks up
        template vars from the enclosing graph's inputs."""
        from operonx.core import END, START
        from operonx.core.ops.graph.graph_op import graph
        from operonx.providers.ops import LLMOp

        with patch("operonx.providers.ops._utils.ResourceHub") as mock_hub:
            mock_hub.instance.return_value = _mock_resource_hub()

            @graph
            def detect(prompt: str, transcript: str):
                c = LLMOp.of(
                    resource="gpt-4",
                    prompt=prompt,
                    fields=["result: str"],
                    parser="json",
                    transcript=transcript,
                )
                START >> c >> END

            node = detect(
                prompt="Analyze this: {transcript}",
                transcript="Hello world",
            )

        llm_op = next(iter(node._ops.values()))
        assert "transcript" in llm_op.inputs

    def test_static_prompt_works_in_graph(self):
        from operonx.core import END, START
        from operonx.core.ops.graph.graph_op import graph
        from operonx.providers.ops import LLMOp

        with patch("operonx.providers.ops._utils.ResourceHub") as mock_hub:
            mock_hub.instance.return_value = _mock_resource_hub()

            @graph
            def detect(transcript: str):
                c = LLMOp.of(
                    resource="gpt-4",
                    prompt="Analyze: {transcript}",
                    fields=["result: str"],
                    parser="json",
                    transcript=transcript,
                )
                START >> c >> END

            node = detect(transcript="Hello world")

        llm_op = next(iter(node._ops.values()))
        assert "transcript" in llm_op.inputs


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
