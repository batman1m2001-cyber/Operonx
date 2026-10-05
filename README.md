# operonx-kb

Knowledge base and document intelligence on [operonx](../Operon): files become versioned documents
whose every character has an address (version, span → page, bbox), indexed for retrieval, and
answers cite spans that were checked against the text they quote.

```bash
uv sync --extra pdf --extra faiss     # core + PDF text layer + FAISS index
PYTHONPATH=../operonx-wt/feat-kb-upstream uv run pytest -q   # until operonx PR #74 merges
```

```python
import operonx
from operonx_kb import CollectionSpec, DenseIndexSpec, KnowledgeBase
from operonx_kb.model.collection import LexicalIndexSpec

operonx.bootstrap(resources="resources.yaml")  # kb_catalog:, kb_blob:, kb_lexical:, embedding:, vector_store:, llm:
kb = KnowledgeBase()
kb.create_collection("handbook", CollectionSpec(
    dense=DenseIndexSpec(embedder="e5", store="vector_store:kb",
                         passage_template="passage: {text}", query_template="query: {text}"),
    lexical=LexicalIndexSpec(),  # SQLite FTS5 or Postgres FTS
    filterable={"dept": "keyword"},
))
await kb.add("handbook", "policy.pdf", key="policy.pdf", tags=["hr"], acl=["team:hr"],
             metadata={"dept": "hr"})
found = await kb.search("handbook", "how many days of leave", mode="hybrid",
                        filter={"acl_any": ["team:hr"]}, reranker="ce")
answer = await kb.ask("handbook", "how many days of leave", "assistant")
answer["citations"]  # each: quote, canonical span, pages, bboxes; unverified ones are in answer["dropped"]
```

```bash
operonx-kb query handbook "how many days of leave" --mode hybrid --tag hr
operonx-kb query wiki "who directed the film that won in 1999" --mode graph   # created with --graph
operonx-kb query handbook "how many days of leave" --answer assistant
operonx-kb eval handbook datasets/handbook.jsonl --mode hybrid
```

## Use it inside your flows

A search is an operonx graph, so it is one step of your own flow — traced with it, no service in
between (`tests/graphs/test_in_a_flow.py`):

```python
search = kb.search_graph("handbook", mode="hybrid")

@graph
def answer_flow(question):
    found = search(query=question, collection="handbook", filter=None, k=5)
    reply = cite(hits=found["hits"])          # your own op
    START >> found >> reply >> END

out = await Operon(answer_flow, params={"question": None}).run(inputs={"question": "..."})
```

For an agent (operonx-agents installed beside it), `kb_tools` gives a read-only `kb_search` /
`kb_read` toolset. The scope comes from your code or the run's context — the model only sends a
query — and `kb_read` checks it again (`tests/graphs/test_agent_tools.py`):

```python
from operonx_kb.tools import kb_tools

tools = kb_tools(kb, "handbook", scope=lambda ctx: {"acl_any": ctx.deps.principals})
agent = Agent(name="hr", model=Model("assistant"), tools=[tools])
```

Studio's Knowledge tab reads a knowledge base through its admin API (extra `admin`), served as
one of the project's services:

```python
import operonx
from operonx.app import Service, asgi
from operonx_kb.admin import kb_admin_app

operonx.bootstrap()  # operonx serve loads no resources for an asgi service
Service("kb_admin", asgi("/kb", port=8021), app=kb_admin_app(llm="assistant"))
```

Outstanding work (K5 visual retrieval needs a GPU): [BACKLOG.md](BACKLOG.md).
Design and phase gates: [PLAN.md](PLAN.md); measured gates: [docs/bench](docs/bench).
Contributor and agent rules: [AGENTS.md](AGENTS.md).
