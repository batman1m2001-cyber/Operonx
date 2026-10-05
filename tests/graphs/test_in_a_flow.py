"""A KB search as one step of a project's own flow (PLAN K7): the search graph is a
subgraph like any other, traced with the flow, no service in between."""

import asyncio

from operonx import END, START, Operon, graph, op

POLICY = "# Leave policy\n\n## Annual leave\n\nEvery employee has twelve days of annual leave per year.\n"


@op
def cite(hits: list) -> dict:
    """The flow's own step after the search: the best passage and where it is."""
    best = hits[0]
    return {"reply": f"{best['text']} ({best['key']}, {' > '.join(best['heading_path'])})"}


def test_the_search_graph_is_a_step_of_a_flow(kbx, tmp_path):
    (tmp_path / "policy.md").write_text(POLICY, encoding="utf-8")
    asyncio.run(kbx.add("docs", str(tmp_path / "policy.md"), key="policy.md"))
    search = kbx.search_graph("docs", mode="hybrid")

    @graph
    def answer_flow(question):
        found = search(query=question, collection="docs", filter=None, k=3)
        reply = cite(hits=found["hits"])
        START >> found >> reply >> END

    engine = Operon(answer_flow, params={"question": None})
    out = asyncio.run(engine.run(inputs={"question": "how many days of annual leave"}))
    assert "$errors" not in out
    assert out["reply"].startswith("Every employee has twelve days")
    assert "(policy.md, Leave policy > Annual leave)" in out["reply"]
