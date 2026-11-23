"""kb.ask(searches=N): the research loop with a scripted model — the researcher's
searches run in order, its kept passages become the answer's sources, the loop stops
on an empty search, a repeated one, or the budget, and usage totals every call."""

import asyncio
import json
import re
from pathlib import Path

import pytest

from operonx_kb import QueryError
from operonx_kb.kb import MAX_SEARCHES

DOCS = Path(__file__).parents[1] / "golden" / "docs"


def run(coro):
    return asyncio.run(coro)


def is_research(messages):
    return messages[0]["content"].startswith("You research")


def passages_of(messages):
    """``n -> text`` of the passages the researcher was shown."""
    body = messages[-1]["content"].split("Passages:\n\n", 1)[1]
    return {
        int(m.group(1)): m.group(2) for m in re.finditer(r"^\[(\d+)\] [^\n]*\n([^\n]*)", body, re.M)
    }


def answer_quoting_first_source(messages):
    user = messages[-1]["content"]
    first = user.split("Sources:\n\n", 1)[1].split("\n", 2)[1]
    quote = re.split(r"(?<=[.!?])\s", first.strip())[0]
    return json.dumps({"answer": f"{quote} [1].", "citations": [{"source": 1, "quote": quote}]})


def researcher(plan):
    """A model that, as the researcher, replies with ``plan(step, passages)`` and, as the
    answerer, quotes its first source."""
    steps = []

    def script(messages):
        if not is_research(messages):
            return answer_quoting_first_source(messages)
        steps.append(messages)
        return json.dumps(plan(len(steps), passages_of(messages)))

    script.steps = steps
    return script


@pytest.fixture
def llm(hub):
    hub.alias("llm:answerer", "fake_llm:scripted")
    return hub.get("fake_llm:scripted")


@pytest.fixture
def loaded(kbx):
    for name in ("engineering_guide.md", "meeting_notes.txt", "quy_trinh_vi.html"):
        run(kbx.add("docs", str(DOCS / name), key=name))
    return kbx


def test_the_second_search_is_the_researchers_and_its_kept_passage_is_the_source(loaded, llm):
    def plan(step, passages):
        if step == 1:
            return {"search": "tombstones purges", "keep": []}
        tomb = [n for n, t in passages.items() if "tombstones" in t]
        return {"search": "", "keep": tomb}

    llm.script = researcher(plan)
    answer = run(loaded.ask("docs", "how do we review code", "answerer", mode="lexical", k=2,
                            searches=3))  # fmt: skip
    assert answer["research"]["searches"] == ["how do we review code", "tombstones purges"]
    assert len(llm.script.steps) == 2
    # the researcher saw the first search's passages, then those plus the second's
    assert "1. how do we review code" in llm.script.steps[0][-1]["content"]
    assert "2. tombstones purges" in llm.script.steps[1][-1]["content"]
    assert [s["key"] for s in answer["sources"]] == ["meeting_notes.txt"]
    assert answer["citations"] and answer["citations"][0]["support"] == "verified"
    calls = llm.messages
    assert len(calls) == 3  # two research calls and the answer
    research_tokens = answer["research"]["usage"]["prompt_tokens"]
    assert answer["usage"]["prompt_tokens"] > research_tokens > 0


def test_a_repeated_search_ends_the_loop(loaded, llm):
    llm.script = researcher(
        lambda step, passages: {"search": "  How do we REVIEW code ", "keep": [1]}
    )
    answer = run(loaded.ask("docs", "how do we review code", "answerer", mode="lexical", k=2,
                            searches=5))  # fmt: skip
    assert answer["research"]["searches"] == ["how do we review code"]
    assert len(answer["sources"]) == 1


def test_the_search_budget_ends_the_loop(loaded, llm):
    llm.script = researcher(lambda step, passages: {"search": f"meeting {step}", "keep": [1, 99]})
    answer = run(
        loaded.ask("docs", "what was decided", "answerer", mode="lexical", k=2, searches=3)
    )
    assert answer["research"]["searches"] == ["what was decided", "meeting 1", "meeting 2"]
    last = llm.script.steps[-1][-1]["content"]
    assert (
        "No search is left." in last
        and "1 more search allowed." in llm.script.steps[1][-1]["content"]
    )
    assert len(answer["sources"]) == 1  # 99 is not a passage; 1 is


def test_no_valid_keep_keeps_every_passage(loaded, llm):
    llm.script = researcher(lambda step, passages: {"search": "", "keep": ["none"]})
    answer = run(loaded.ask("docs", "how do we review code", "answerer", mode="hybrid", k=4,
                            searches=2))  # fmt: skip
    shown = passages_of(llm.script.steps[0])
    assert len(shown) >= 2
    assert len({s["key"] for s in answer["sources"]}) >= 2


def test_one_search_is_the_single_shot_answer(loaded, llm):
    llm.script = researcher(lambda step, passages: pytest.fail("no researcher with searches=1"))
    answer = run(loaded.ask("docs", "how do we review code", "answerer", mode="lexical", k=2))
    assert "research" not in answer and len(llm.messages) == 1


@pytest.mark.parametrize("searches", [0, MAX_SEARCHES + 1])
def test_searches_out_of_range_is_refused(loaded, llm, searches):
    with pytest.raises(QueryError, match="searches must be 1 to"):
        run(loaded.ask("docs", "anything", "answerer", searches=searches))


def test_a_researcher_reply_that_does_not_parse_fails_loudly(loaded, llm):
    llm.script = lambda messages: "let me think" if is_research(messages) else "{}"
    with pytest.raises(QueryError, match="ParserError"):
        run(loaded.ask("docs", "anything", "answerer", mode="lexical", k=2, searches=2))
