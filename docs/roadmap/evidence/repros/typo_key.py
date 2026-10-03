"""Common typos: a misspelled output key, a misspelled input kwarg, an unwired op."""
import asyncio

from operonx import END, START, Operon, graph, op


@op
def make(x: int) -> dict:
    return {"total": x + 1}


@op
def show(total: int = 0) -> dict:
    return {"text": f"total={total}"}


@graph
def typo_output(x):
    m = make(x=x)
    s = show(total=m["totl"])  # typo in the output key
    START >> m >> s >> END


@graph
def typo_input(x):
    m = make(x=x)
    s = show(totl=m["total"])  # typo in the input name
    START >> m >> s >> END


@graph
def typo_param(x):
    m = make(x=x)
    s = show(total=m["total"])
    START >> m >> s >> END


async def main():
    for g, inputs in ((typo_output, {"x": 1}), (typo_input, {"x": 1}), (typo_param, {"xx": 1})):
        try:
            out = await Operon(g, params={"x": None}).run(inputs=inputs)
            print(getattr(g, "__name__", g), "->", out.get("text"), list(out.get("$errors", {})))
        except Exception as e:
            print("RAISED", type(e).__name__, str(e)[:250])


asyncio.run(main())
