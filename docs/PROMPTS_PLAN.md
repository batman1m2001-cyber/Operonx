# Prompts plan — one home, names, versions

Status: **done 2026-11-21 (P1–P5).** P1 and P2 are operonx 1.40.0 (`operonx.prompts`, `operonx prompt`, `attrs.prompts` on the run row); 1.41.0 added the prompt chosen at run time (P4 needed it). P3 is operonx-studio #228. P4 is the callbot's `prompts/` and `agent.yaml`. P5 is mr-finance's `prompts/`, with one part deliberately not done (see Outcome). P6 was not asked for. Written when nothing was built. The owner asked for it after the studio's Prompts page showed no prompt text and took most of a minute.

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

## Outcome

| Part | Where | What it came to |
|---|---|---|
| P1, P2 | operonx 1.40.0 (#292) | `prompts/<name>.md`, `{{var}}`, `{{> include}}`, versions as files, labels in `labels.toml`, `operonx prompt list/show/publish/label/diff/log`, `attrs.prompts` on the run row. |
| P3 | operonx-studio #228, `docs/PROMPTS_PAGE.md` there | The Prompts page lists the named prompts with text, versions, labels, diff, save / publish / serve. The list answers in 51 ms on the callbot. |
| P4 | callbot, `docs/PROMPTS.md` there; operonx 1.41.0 | `prompts.yaml` is `agent.yaml`; five prompt files (`<agent>/analyzer`, `classifier/system`, `classifier/user`). All 28 per-state system prompts and the user message are the same bytes (golden test); 338 tests pass (335 + 3). |
| P5 | mr-finance, `docs/PROMPTS.md` there | 16 prompt files; 10 constants read them; all the same bytes (golden test). |

Three things differ from the table above:

- **P1's `operonx check` rule is the build itself.** `prompt("x")` raises `PromptNotFound` when the graph is built, which `operonx check` already runs; no separate rule was added.
- **P4: the per-state prompt is a template filled by code, not by `{{> intent_definitions}}`.** The intents, examples and rules are the agent's data in `agent.yaml`, different per state, so they are variables (`{{intent_definitions}}`), not included prompts. And because the callbot chooses its prompt per call, the op could not name it when the graph is built: 1.41.0 makes `.render()` return a string that remembers its prompts, and the LLM op records them (`attrs.prompts[].input`).
- **P5: the governed CIO prompts keep their database history; the label does not gate them.** `prompts/cio/` is version 1 of the `prompts` configuration kind. Its later versions are made by an Admin in the running app (two people, the eval set). Writing those into `prompts/labels.toml` would have a running server edit its own checkout, which a `git pull` deploy then conflicts with. The existing gate is unchanged; the admin screen shows the same history because nothing about it moved.

Left open: the studio still lists a resource's YAML file whose field is named like a prompt (built for the callbot's `prompts.yaml`, now unused by it); `operonx init --template chat` still writes a `SYSTEM` constant; operonx-agents on PyPI (0.3.1) predates `Agent(instructions=prompt(...))`, so mr-finance passes `.text`.

## Open choices (my pick first)

1. Versions as files in git (pick) or Langfuse as the source of truth.
2. `{{var}}` (pick: braces in prompts are usually JSON the model must answer with, as mr-finance found) or the `{var}` LLMOp has today — kept working for inline prompts either way.
3. One file per prompt (pick) or one YAML of many.

## Not in this plan

Prompt evaluation gates (EVALS_PLAN owns evals; P5 only wires the existing gate to the label), A/B traffic splitting between versions, a prompt editor for non-engineers outside the studio.
