import asyncio
import os
import sys

sys.path[:0] = ["/home/thanglq/operonx-dogfood/common"]
import fakellm  # noqa: E402

os.environ.update({"LLM_BASE_URL": fakellm.start(), "LLM_API_KEY": "sk-fake"})
import operonx  # noqa: E402
from operonx import END, START, Operon, graph  # noqa: E402
from operonx.providers.ops import LLMOp  # noqa: E402


@graph
def g(draft):
    llm = LLMOp.of(resource="assistant", prompt='Rate: {draft}. Answer as JSON {"score": 1}', draft=draft)
    START >> llm >> END


@graph
def h(note):
    llm = LLMOp.of(resource="assistant", prompt="Summarize: {note}", note=note)
    START >> llm >> END


async def main():
    operonx.bootstrap(resources="/home/thanglq/operonx-dogfood/p1_chain/resources.yaml", env=False)
    fakellm.SCRIPT[:] = ["ok", "ok"]
    try:
        e = Operon(g, params={"draft": None})
        out = await e.run(inputs={"draft": "x"})
        print("literal braces ->", out.get("content"), [v.splitlines()[-1] for v in out.get("$errors", {}).values()])
    except Exception as ex:
        print("literal braces RAISED at build:", type(ex).__name__, str(ex)[:300])
    # a template value that itself contains braces (user text) - safe?
    out = await Operon(h, params={"note": None}).run(inputs={"note": "user wrote {name} here"})
    print("braces in value ->", out.get("content"), list(out.get("$errors", {})))
    print("sent:", fakellm.SEEN[-1]["messages"][-1]["content"] if fakellm.SEEN else None)


asyncio.run(main())
