# operonx-agents

Typed LLM steps and agents on the [operonx](../Operon) workflow engine.
Design: `Operon/docs/roadmap/track3_agents.md` §4; plan and phases:
`Operon/docs/AGENTS_V2_PLAN.md`.

Two front doors over one model layer:

- **`llm_step`**: a typed, deadline-bounded model call as a graph op. The
  graph owns control flow (the callbot's front door).
- **`Agent` + `Runner`**: a model-driven tool loop in one op, with child
  executions in the trace (phase A3).

Phase A2 (this state) ships tools, the model layer, `llm_step` and
`operonx-agents probe`.

```python
from operonx_agents import Choice, Model, ModelSettings, llm_step

classify = llm_step(
    model=Model("inhouse", deadline=0.9, settings=ModelSettings(logprobs=True)),
    system="{analyzer_system_prompt}",
    user="{intent_prompt}",
    output=Choice(from_input="allowed_intents", field="intent"),
    on_timeout="fallback",
    on_invalid="fallback",
)
# inside a @graph: c = classify(analyzer_system_prompt=..., intent_prompt=..., allowed_intents=...)
# outputs: value, confidence, outcome, error, usage, latency_ms, model_used
```
