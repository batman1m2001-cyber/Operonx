"""Tests: ask() inside @graph with if_() branching.

Verifies:
- No deadlock when ask() nested after if_()
- Skip path works correctly
- Validators format validation
- @-prefix default values
"""

import asyncio
from unittest.mock import patch

import pytest

from operonx.core import END, START, Operon, graph
from operonx.core.ops import op
from operonx.core.ops.flow import if_
from operonx.providers.ops import ask
from tests.internal.providers.test_extract_retry import make_mock_hub

# ── Tests: ask() after if_() branching ──


class TestExtractAfterBranch:
    def _run_branch_test(self, needs_llm, responses, expected_final):
        """Helper: build graph with if_() + extract, run with mock LLM."""

        @op
        def detect(text: str) -> dict:
            return {"needs_llm": needs_llm, "quick_result": "skipped" if not needs_llm else None}

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
                e = ask(
                    resource="mock",
                    template={"user": "{text}"},
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
        """if_() routes to skip path → extract NOT run, no deadlock."""
        self._run_branch_test(
            needs_llm=False,
            responses=["<result>should_not_run</result>"],
            expected_final="skipped",
        )

    def test_llm_path_no_deadlock(self):
        """if_() routes to LLM path → extract runs without deadlock."""
        self._run_branch_test(
            needs_llm=True,
            responses=["<result>found</result>"],
            expected_final="found",
        )

    def test_llm_path_with_validators(self):
        """LLM returns valid value → passes validators."""

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
                e = ask(
                    resource="mock",
                    template={"user": "{text}"},
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
        """LLM returns invalid → @fallback default used."""

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
                e = ask(
                    resource="mock",
                    template={"user": "{text}"},
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


# ── Tests: validators format validation ──


class TestValidatorsFormat:
    def test_invalid_validators_returns_error(self):
        """validators must be dict, not list."""
        from operonx.core.ops.transform.parser_op import ParserOp

        parser = ParserOp(format="xml", extract=["result: str"])
        result = asyncio.run(
            parser._process(
                text="<result>hello</result>",
                validators=["not", "a", "dict"],
            )
        )
        assert result.get("error") is not None
        assert "validators must be a dict" in result["error"]

    def test_valid_validators_dict(self):
        """Proper dict validators work."""
        from operonx.core.ops.transform.parser_op import ParserOp

        parser = ParserOp(format="xml", extract=["result: str"])
        result = asyncio.run(
            parser._process(
                text="<result>confirm</result>",
                validators={"result": ["confirm", "deny", "@fallback"]},
            )
        )
        assert result["error"] is None
        assert result["result"] == "confirm"

    def test_validators_default_on_reject(self):
        """@-prefix value used as default when validation fails."""
        from operonx.core.ops.transform.parser_op import ParserOp

        parser = ParserOp(format="xml", extract=["result: str"])
        result = asyncio.run(
            parser._process(
                text="<result>unknown_intent</result>",
                validators={"result": ["confirm", "deny", "@fallback"]},
            )
        )
        assert result["error"] is None
        assert result["result"] == "fallback"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
