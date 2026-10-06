"""Where the step's p95 gap to control comes from: the shadow replay's
recorded turns through five arms, rotated per turn, 3 passes.

    PYTHONPATH=<operonx main> uv run python scripts/shadow_latency.py
"""

import asyncio
import statistics
import sys
import time

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))
import operonx
from dotenv import load_dotenv
from operonx.core import END, START, Operon, graph

from operonx_agents import Choice, Model, ModelSettings, llm_step
from scripts.shadow_replay import CALLBOT_ENV, RESOURCES, control, pct, recorded_turns


def _step(logprobs):
    return llm_step(
        model=Model("inhouse", deadline=0.9, settings=ModelSettings(logprobs=logprobs)),
        system="{analyzer_system_prompt}",
        user="{intent_prompt}",
        output=Choice(from_input="allowed_intents", field="intent"),
        on_timeout="fallback",
        on_invalid="fallback",
    )


STEP_LP, STEP_NOLP = _step(True), _step(False)


@graph
def step_lp(system=None, user=None, allowed=None):
    llm_classify = STEP_LP(
        analyzer_system_prompt=system, intent_prompt=user, allowed_intents=allowed
    )
    START >> llm_classify >> END


@graph
def step_nolp(system=None, user=None, allowed=None):
    llm_classify = STEP_NOLP(
        analyzer_system_prompt=system, intent_prompt=user, allowed_intents=allowed
    )
    START >> llm_classify >> END


def mk(g):
    return Operon(g, params={"system": None, "user": None, "allowed": None})


async def main():
    load_dotenv(CALLBOT_ENV, override=False)
    operonx.bootstrap(resources=RESOURCES, env=False)
    turns = recorded_turns()
    from operonx.core.registry.resource_hub import ResourceHub

    llm = ResourceHub.instance().get("llm:inhouse")
    eng = {
        "control": Operon(control, params={"system": None, "user": None}),
        "step_lp": mk(step_lp),
        "step_nolp": mk(step_nolp),
    }

    async def raw_native(t):
        schema = {
            "type": "object",
            "properties": {"intent": {"type": "string", "enum": t["allowed"]}},
            "required": ["intent"],
            "additionalProperties": False,
        }
        await llm.generate(
            messages=[
                {"role": "system", "content": t["system"]},
                {"role": "user", "content": t["user"]},
            ],
            temperature=0.0,
            response_format={
                "type": "json_schema",
                "json_schema": {"name": "intent", "schema": schema, "strict": True},
            },
        )

    async def raw_plain(t):
        await llm.generate(
            messages=[
                {"role": "system", "content": t["system"]},
                {"role": "user", "content": t["user"]},
            ],
            temperature=0.0,
        )

    arms = {
        "control": lambda t: eng["control"].run({"system": t["system"], "user": t["user"]}),
        "step_lp": lambda t: eng["step_lp"].run(
            {"system": t["system"], "user": t["user"], "allowed": t["allowed"]}
        ),
        "step_nolp": lambda t: eng["step_nolp"].run(
            {"system": t["system"], "user": t["user"], "allowed": t["allowed"]}
        ),
        "raw_native": raw_native,
        "raw_plain": raw_plain,
    }
    names = list(arms)
    ms = {n: [] for n in names}
    for t in turns[:3]:
        for n in names:
            await arms[n](t)
    for p in range(3):
        for i, t in enumerate(turns):
            k = (i + p) % len(names)
            for n in names[k:] + names[:k]:
                s = time.perf_counter()
                await arms[n](t)
                ms[n].append((time.perf_counter() - s) * 1000)
    for n in names:
        p50, p95 = statistics.median(ms[n]), pct(ms[n], 0.95)
        print(f"{n:11s} n={len(ms[n])} p50={p50:6.1f} p95={p95:6.1f}")


if __name__ == "__main__":
    asyncio.run(main())
