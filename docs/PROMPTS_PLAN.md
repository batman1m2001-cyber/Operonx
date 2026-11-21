# Prompts plan — one home, names, versions

Status: **proposed** (2026-11-21). Nothing is built. The owner asked for it after the studio's Prompts page showed no prompt text and took most of a minute.

## Why

A prompt is the most-edited text in a project and today it has no home.

| Project | Where prompts are today |
|---|---|
| callbot | `src/agents/<agent>/prompts.yaml` — the whole agent (intents, scenario, response templates): 2,542 lines for educa_reminder, about 100 of them prompts. The system prompt the model gets is assembled in code per state (`agents/_prompt.py`); no file holds it. |
| mr-finance | `SYSTEM = …` constants in 6 modules, `instructions=` inline in agents, and a home-made versioned store (`views/prompts.py`, its own tables, two-person approval). |
| tcb-wepro | one constant in `chat/_prompts.py`. |
| engine | `LLMOp(prompt=)` takes a string or `{system, user}`. `LangfusePromptManager` exists, no op uses it. A run does not record which prompt, or which version, it used. |

So the studio guesses: it opens up to 2,000 runs and calls "the prompt" whatever input is identical in all of them. A chat op's only input is `messages`, which never is.

## The design

**1. A prompt is a named file.** `prompts/<name>.md` in the project, the text as the model gets it, `{{variable}}` for what is filled in. Optional front matter for what is not text.

```markdown
---
description: The intake conversation's system prompt
variables: [customer_name]
---
Bạn là chuyên viên của OneWealth… Khách hàng: {{customer_name}}
```

**2. Ops ask for it by name.**

```python
LLMOp.of(resource="assistant", prompt=prompt("intake.system"), customer_name=name)
AgentOp.of(..., instructions=prompt("declare.agent"))
```

`prompt()` is resolved when the graph is built (a missing name fails `operonx check`, not a call at 3 am). A composed prompt includes others: `{{> guard}}`.

**3. Versions and labels, as Langfuse has them.** Publishing a prompt freezes its text as the next number; a label points at a number.

```
prompts/intake.system.md            the working text (what `latest` is)
prompts/.versions/intake.system/3.md
prompts/labels.toml                 [intake.system] production = 3, staging = 4
```

- The runtime reads the label of its environment (`OPERONX_PROMPT_LABEL`, default `production`; none published yet → the working text).
- Rollback = move the label. `operonx prompt publish|label|diff|log`.
- Everything is files in the project's git: no service to run (a bank has none), history and review are git's. Langfuse is an optional mirror (`operonx prompt push|pull`, through the existing `LangfusePromptManager`), not the source.

**4. A run says what it used.** The node row of an LLM/agent op carries `prompt: {name, version, label}`. Then "which version answered this" and "how did v4 do against v3" are queries, not guesses.

**5. The studio's Prompts page reads the declaration.** A list of prompts by name; for one: the text, its versions with a diff between any two, the ops that use it, runs and cost per version, try an edit on recorded inputs, publish, move a label. No scan of the run store to find out what the prompt is.

## Parts

| Part | What | Done when |
|---|---|---|
| P1 engine | `prompt()`, the `prompts/` loader, includes, `operonx check` rule, the run's `prompt` field | a graph using `prompt("x")` runs; the run row names `x` and its version; a missing name fails check |
| P2 versions | publish / label / diff / log, `labels.toml`, label by environment | publish twice, point `production` at 1, the runtime serves 1; move it, serves 2, no code change |
| P3 studio | Prompts page on the declaration (list, text, versions, diff, used by, per-version runs, publish/label) | opens under 1 s on a 9,000-run project and shows every prompt's text |
| P4 callbot | `prompts.yaml` → `agent.yaml` (intents, scenario, templates) + `prompts/*.md`; the per-state system prompt becomes a template with `{{> intent_definitions}}` | the bytes sent to the model are identical before and after (golden test per state); 335 tests pass |
| P5 mr-finance | the 6 constants and `views/prompts.py` versions move into the store; two-person approval gates the `production` label | eval set passes; the admin screen shows the same history |
| P6 mirror | optional Langfuse push/pull | a published version appears in Langfuse with the same number and label |

Order: P1 → P2 → P3 → P4 → P5; P6 only if someone asks.

## Open choices (my pick first)

1. Versions as files in git (pick) or Langfuse as the source of truth.
2. `{{var}}` (pick: braces in prompts are usually JSON the model must answer with, as mr-finance found) or the `{var}` LLMOp has today — kept working for inline prompts either way.
3. One file per prompt (pick) or one YAML of many.

## Not in this plan

Prompt evaluation gates (EVALS_PLAN owns evals; P5 only wires the existing gate to the label), A/B traffic splitting between versions, a prompt editor for non-engineers outside the studio.
