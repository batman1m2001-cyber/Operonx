"""Tests: LLMOp structured mode inside @graph with if_() branching.

Verifies:
- No deadlock when LLMOp(fields=...) nested after if_()
- Skip path works correctly
- Validators format validation (via parsing.apply_validators)
- @-prefix default values

Replaces the pre-1.0 tests that exercised the removed ``ask()`` helper.
"""

import asyncio
from unittest.mock import patch

import pytest

from operonx.core import END, START, Operon, graph
from operonx.core.ops import op
from operonx.core.ops.flow import if_
from operonx.providers.ops import LLMOp
from tests.internal.providers.test_extract_retry import make_mock_hub


class TestExtractAfterBranch:
    def _run_branch_test(self, needs_llm, responses, expected_final):
        """Helper: build graph with if_() + LLMOp(fields=...), run with mock LLM."""

        @op
        def detect(text: str) -> dict:
            return {
                "needs_llm": needs_llm,
                "quick_result": "skipped" if not needs_llm else None,
            }

        @op
        def skip(quick_result: str = None) -> dict:
            return {"result": quick_result}

        @op
        def merge(result: str = None, llm_result: str = None) -> dict:
            return {"final": result or llm_result or "none"}

        mock_hub, _ = make_mock_hub(responses)
        with patch("operonx.providers.ops._utils.ResourceHub") as mock_cls:
            mock_cls.instance.return_value = mock_hub

            @graph
            def wf(text):
                d = detect(text=text)
                e = LLMOp.of(
                    resource="mock",
                    prompt={"user": "{text}"},
                    fields=["result: str"],
                    parser="xml",
                    text=text,
                )
                s = skip(quick_result=d["quick_result"])
                router = if_(d["needs_llm"] == True, e).else_(s)  # noqa: E712
                m = merge(result=s["result"], llm_result=e["result"])

                START >> d >> router
                router >> e >> ~m >> END
                router >> s >> ~m

            g = wf(text="test")
            engine = Operon(g)
            result = asyncio.run(engine.run(inputs={}))

        assert result.get("final") == expected_final

    def test_skip_path_no_deadlock(self):
        self._run_branch_test(
            needs_llm=False,
            responses=["<result>should_not_run</result>"],
            expected_final="skipped",
        )

    def test_llm_path_no_deadlock(self):
        self._run_branch_test(
            needs_llm=True,
            responses=["<result>found</result>"],
            expected_final="found",
        )

    def test_llm_path_with_validators(self):
        @op
        def detect(text: str) -> dict:
            return {"needs_llm": True, "quick_result": None}

        @op
        def skip(quick_result: str = None) -> dict:
            return {"result": quick_result}

        @op
        def merge(result: str = None, llm_result: str = None) -> dict:
            return {"final": result or llm_result or "none"}

        mock_hub, _ = make_mock_hub(["<result>confirm</result>"])
        with patch("operonx.providers.ops._utils.ResourceHub") as mock_cls:
            mock_cls.instance.return_value = mock_hub

            @graph
            def wf(text):
                d = detect(text=text)
                e = LLMOp.of(
                    resource="mock",
                    prompt={"user": "{text}"},
                    fields=["result: str"],
                    parser="xml",
                    validators={"result": ["confirm", "deny", "@fallback"]},
                    text=text,
                )
                s = skip(quick_result=d["quick_result"])
                router = if_(d["needs_llm"] == True, e).else_(s)  # noqa: E712
                m = merge(result=s["result"], llm_result=e["result"])

                START >> d >> router
                router >> e >> ~m >> END
                router >> s >> ~m

            g = wf(text="yes")
            engine = Operon(g)
            result = asyncio.run(engine.run(inputs={}))

        assert result.get("final") == "confirm"

    def test_llm_path_validators_reject_uses_default(self):
        @op
        def detect(text: str) -> dict:
            return {"needs_llm": True, "quick_result": None}

        @op
        def skip(quick_result: str = None) -> dict:
            return {"result": quick_result}

        @op
        def merge(result: str = None, llm_result: str = None) -> dict:
            return {"final": result or llm_result or "none"}

        mock_hub, _ = make_mock_hub(["<result>unknown_garbage</result>"])
        with patch("operonx.providers.ops._utils.ResourceHub") as mock_cls:
            mock_cls.instance.return_value = mock_hub

            @graph
            def wf(text):
                d = detect(text=text)
                e = LLMOp.of(
                    resource="mock",
                    prompt={"user": "{text}"},
                    fields=["result: str"],
                    parser="xml",
                    validators={"result": ["confirm", "deny", "@fallback"]},
                    text=text,
                )
                s = skip(quick_result=d["quick_result"])
                router = if_(d["needs_llm"] == True, e).else_(s)  # noqa: E712
                m = merge(result=s["result"], llm_result=e["result"])

                START >> d >> router
                router >> e >> ~m >> END
                router >> s >> ~m

            g = wf(text="test")
            engine = Operon(g)
            result = asyncio.run(engine.run(inputs={}))

        assert result.get("final") == "fallback"


class TestParsingPureFunctions:
    """Direct tests of the parsing pure functions used by LLMOp inline.
    Previously covered by ParserOp._process tests."""

    def test_invalid_validators_returns_error(self):
        from operonx.providers.parsing import ExtractField, parse_and_extract

        fields = [ExtractField.from_string("result: str")]
        result = parse_and_extract(
            text="<result>hello</result>",
            parser="xml",
            fields=fields,
            validators=["not", "a", "dict"],  # type: ignore[arg-type]
        )
        assert result.get("error") is not None
        assert "validators must be a dict" in result["error"]

    def test_valid_validators_dict(self):
        from operonx.providers.parsing import ExtractField, parse_and_extract

        fields = [ExtractField.from_string("result: str")]
        result = parse_and_extract(
            text="<result>confirm</result>",
            parser="xml",
            fields=fields,
            validators={"result": ["confirm", "deny", "@fallback"]},
        )
        assert result["error"] is None
        assert result["result"] == "confirm"

    def test_validators_default_on_reject(self):
        from operonx.providers.parsing import ExtractField, parse_and_extract

        fields = [ExtractField.from_string("result: str")]
        result = parse_and_extract(
            text="<result>unknown_intent</result>",
            parser="xml",
            fields=fields,
            validators={"result": ["confirm", "deny", "@fallback"]},
        )
        assert result["error"] is None
        assert result["result"] == "fallback"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
