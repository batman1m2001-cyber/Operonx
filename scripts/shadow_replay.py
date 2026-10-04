"""Callbot shadow replay: recorded classifier turns through ``llm_step`` and
through the callbot's ``LLMOp`` path, intent by intent (A2 gate).

The turns are the callbot's own ``llm_classify`` executions as its tracer
wrote them to the team ClickHouse (database ``callbot_traces``, table
``nodes``, read-only SELECT): the exact system prompt and intent prompt it
sent, and the intent it got. The allow-list is read back from the
``<allowed_intents>`` tag of that prompt.

Two arms per turn, alternated so gateway drift hits both:

  control  the callbot's op as it is on refactor/operonx-studio
           (src/agents/graph.py): LLMOp.of(resource="inhouse",
           prompt={system, user}, fields=["intent: str"], parser="json",
           max_retries=1)
  step     the A6 shape: llm_step(model=Model("inhouse", deadline=0.9,
           logprobs), output=Choice(from_input="allowed_intents",
           field="intent"), on_timeout="fallback", on_invalid="fallback")
           — native json_schema, as `probe` says inhouse declares

Latency is timed outside the engine for both. Credentials: ClickHouse from
Operon/secrets.yaml, the gateway from the callbot's .env; neither is
printed.

    PYTHONPATH=<operonx main> uv run python scripts/shadow_replay.py --passes 1
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import statistics
import time
from pathlib import Path

import httpx
import operonx
import yaml
from dotenv import load_dotenv
from operonx.core import END, START, Operon, graph
from operonx.providers.ops import LLMOp

from operonx_agents import Choice, Model, ModelSettings, llm_step

ROOT = Path(__file__).resolve().parents[1]
SECRETS = Path("/home/thanglq/Operon/secrets.yaml")
CALLBOT_ENV = Path("/home/thanglq/callbot-wt/refactor/.env")
RESOURCES = ROOT / "scripts" / "shadow_resources.yaml"

QUERY = """
SELECT trace_id, op_id, start_time, duration_ms,
       JSONExtractString(inputs, 'analyzer_system_prompt') AS system,
       JSONExtractString(inputs, 'intent_prompt') AS user,
       JSONExtractString(outputs, 'intent') AS intent
FROM nodes
WHERE op_name = 'llm_classify' AND status = 'ok'
ORDER BY written_at, trace_id, seq
FORMAT JSON
"""


def recorded_turns() -> list:
    c = yaml.safe_load(SECRETS.read_text())["clickhouse"]["callbot_traces"]
    r = httpx.post(
        f"http://{c['host']}:{c['http_port']}/",
        params={"database": c["database"], "readonly": "1"},
        content=QUERY,
        auth=(c["user"], c["password"]),
        timeout=60,
    )
    r.raise_for_status()
    rows = r.json()["data"]
    for row in rows:
        m = re.search(r"<allowed_intents>(.*?)</allowed_intents>", row["user"], re.S)
        row["allowed"] = [s.strip() for s in m.group(1).split(",") if s.strip()] if m else None
        u = re.search(r"<customer>(.*?)</customer>", row["user"], re.S)
        row["utterance"] = u.group(1).strip() if u else None
    return rows


@graph
def control(system=None, user=None):
    llm_classify = LLMOp.of(
        resource="inhouse",
        prompt={"system": "{analyzer_system_prompt}", "user": "{intent_prompt}"},
        fields=["intent: str"],
        parser="json",
        max_retries=1,
        analyzer_system_prompt=system,
        intent_prompt=user,
    )
    START >> llm_classify >> END


classify = llm_step(
    model=Model("inhouse", deadline=0.9, settings=ModelSettings(logprobs=True)),
    system="{analyzer_system_prompt}",
    user="{intent_prompt}",
    output=Choice(from_input="allowed_intents", field="intent"),
    on_timeout="fallback",
    on_invalid="fallback",
)


@graph
def step(system=None, user=None, allowed=None):
    llm_classify = classify(
        analyzer_system_prompt=system, intent_prompt=user, allowed_intents=allowed
    )
    START >> llm_classify >> END


def pct(values, q):
    s = sorted(values)
    return s[min(len(s) - 1, int(round(q * (len(s) - 1))))]


async def main(args) -> None:
    load_dotenv(CALLBOT_ENV, override=False)
    operonx.bootstrap(resources=RESOURCES, env=False)
    turns = recorded_turns()
    usable = [t for t in turns if t["allowed"] and t["system"] and t["user"]]
    print(
        f"recorded llm_classify turns: {len(turns)}, usable: {len(usable)}, "
        f"traces: {len({t['trace_id'] for t in turns})}, "
        f"distinct utterances: {len({t['utterance'] for t in turns})}",
        flush=True,
    )
    eng_c = Operon(control, params={"system": None, "user": None})
    eng_s = Operon(step, params={"system": None, "user": None, "allowed": None})
    first = usable[0]
    await eng_c.run({"system": first["system"], "user": first["user"]})  # connection warm-up
    await eng_s.run({"system": first["system"], "user": first["user"], "allowed": first["allowed"]})
    rows = []
    for p in range(args.passes):
        for i, t in enumerate(usable):
            order = ("control", "step") if (i + p) % 2 == 0 else ("step", "control")
            row = {
                "pass": p,
                "i": i,
                "trace_id": t["trace_id"],
                "utterance": t["utterance"],
                "recorded": t["intent"],
                "allowed": t["allowed"],
            }
            for arm in order:
                start = time.perf_counter()
                if arm == "control":
                    out = await eng_c.run({"system": t["system"], "user": t["user"]})
                    row["control"] = out.get("intent")
                    row["control_error"] = bool(out.get("$errors")) or out.get("error")
                else:
                    out = await eng_s.run(
                        {"system": t["system"], "user": t["user"], "allowed": t["allowed"]}
                    )
                    row["step"] = out.get("value")
                    row["step_outcome"] = out.get("outcome")
                    row["confidence"] = out.get("confidence")
                row[f"{arm}_ms"] = (time.perf_counter() - start) * 1000.0
            rows.append(row)
        print(f"pass {p + 1}/{args.passes} done", flush=True)

    def summary(sel):
        agree = sum(r["control"] == r["step"] for r in sel)
        return {
            "pairs": len(sel),
            "agreement": agree,
            "agreement_pct": round(100.0 * agree / len(sel), 2),
            "step_vs_recorded": sum(r["step"] == r["recorded"] for r in sel),
            "control_vs_recorded": sum(r["control"] == r["recorded"] for r in sel),
            "control_in_allow_list": sum(r["control"] in r["allowed"] for r in sel),
            "step_outcomes": {
                k: sum(r["step_outcome"] == k for r in sel)
                for k in {r["step_outcome"] for r in sel}
            },
            "control_p50_ms": round(statistics.median(r["control_ms"] for r in sel), 1),
            "control_p95_ms": round(pct([r["control_ms"] for r in sel], 0.95), 1),
            "step_p50_ms": round(statistics.median(r["step_ms"] for r in sel), 1),
            "step_p95_ms": round(pct([r["step_ms"] for r in sel], 0.95), 1),
            "step_confidence_p50": round(
                statistics.median(r["confidence"] for r in sel if r["confidence"] is not None), 4
            )
            if any(r["confidence"] is not None for r in sel)
            else None,
            "disagreements": [
                {k: r[k] for k in ("utterance", "recorded", "control", "step", "confidence")}
                for r in sel
                if r["control"] != r["step"]
            ],
        }

    report = {
        "source": "ClickHouse callbot_traces.nodes, op_name=llm_classify, status=ok",
        "recorded_turns": len(turns),
        "usable_turns": len(usable),
        "traces": len({t["trace_id"] for t in turns}),
        "distinct_utterances": len({t["utterance"] for t in turns}),
        "recorded_window": [
            min(t["start_time"] for t in turns),
            max(t["start_time"] for t in turns),
        ],
        "passes": args.passes,
        "first_pass": summary([r for r in rows if r["pass"] == 0]),
        "all_passes": summary(rows),
    }
    out = ROOT / "results" / args.out
    out.write_text(json.dumps(report, indent=1, ensure_ascii=False) + "\n")
    print(
        json.dumps(
            {k: v for k, v in report.items() if k != "distinct_utterances"},
            indent=1,
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--passes", type=int, default=1)
    p.add_argument("--out", default="shadow_replay.json")
    asyncio.run(main(p.parse_args()))
