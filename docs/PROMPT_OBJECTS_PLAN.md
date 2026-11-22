# Prompt objects plan

Status: proposed 2026-11-22 (replaces the first draft, "prompt layers", #306). Nothing
is built yet; implementation waits for the owner's "go".

## What the owner asked for

> A prompt is no longer text. It is an object with an id, and each object is a
> combination of links between components.

So the unit is not a string with holes. There are two kinds of object:

- a **text object**: a piece of text with an id;
- a **composite object**: an id plus ordered links to other objects.

A prompt sent to a model is a composite. Text is produced only at the last step,
when the model is called. Before that, and on every run row, a prompt is a tree of
ids.

## Audit (2026-11-22): why the text model breaks

| Project | Where the text lives | Syntax | Prompt inside a variable | Seen by studio |
|---|---|---|---|---|
| educa-reminder-agent (callbot) | `prompts/classifier/{system,user}.md`, `prompts/<agent>/analyzer.md` | `{{x}}`, plus `{x}` in YAML rules | yes: `{{analyzer}}` | yes |
| mr-finance | 16 `.md` in `prompts/`, ~100 lines of prompt text as Python constants | `{{x}}` (then `.format`), `<<x>>` (CIO), `{x}` (`.format` on constants) | yes: `facts`, `data`, `recount`, `voice`, `english`, `remembered`, `duties` | partly |
| qc-snatcher | 30 `*.prompt` files, flat dict by stem | `{x}`, `── DYNAMIC ──` split marker | yes: `time_prompt=`, `agreement_prompt=` | no |
| meeting-prep-operonx | 3 Python constants | `{x}` inline | `instructions()` joins text | no |
| meeting-prep-brd | `*.prompt` files by stem | `{x}` inline | no | no |

Evidence:

- **callbot**
  - `prompts/classifier/system.md` is `{{analyzer}}\n\n{{intent_definitions}}\n\n{{few_shot}}\n{{rules}}`.
  - `src/agents/_prompt.py:83-93` fills `analyzer` with the prompt `<agent>/analyzer`. It builds `rules` in code, running each `config.classification_rules` entry through `rule.format(allowed_intents_csv=…)` (`src/agents/ahamove_hr/agent.yaml:1387`).
  - The Prompts page shows two unrelated entries, and `{allowed_intents_csv}` appears nowhere.
- **mr-finance**
  - `src/assistant/turn.py:123` loads `assistant/rules`. `turn.py:240-265` fills 12 variables; 7 of them are prompt text kept as Python constants (`DUTIES` :99, `FACTS_MODEL` :124, `FACTS_TEMPLATE` :130, `DATA_RULE` :138, `RECOUNT` :149, `ENGLISH` :156, `VOICE` :164, `REMEMBERED` :199).
  - The choice between them is `if` in Python: `facts=FACTS_MODEL if ctx.reads else FACTS_TEMPLATE`, `voice=VOICE if ctx.voice else ""`.
  - CIO: `src/views/agents.py:251` → `views/prompts.py:77` replaces `<<k>>`. These prompts are DB-governed (two people + eval set).
  - `src/copilot/router.py:136` builds text inline. `research/suggest.py` and `trial/daily.py` `.format` a `PromptText`.
- **qc-snatcher**
  - `src/_shared/prompts.py` loads `*.prompt` into `PROMPTS[stem]` and splits system/user on `── DYNAMIC ──`.
  - `src/cases/raba/graph.py` passes whole prompts as arguments.
- **meeting-prep-operonx**
  - `src/prepare/_prompts.py` holds constants; `graph.py` has the inline `"Company: {company}\n\nEvidence:\n{evidence}"`.
  - `memory.py:54` returns `RESEARCH + HEADING + notes`.
- **meeting-prep-brd**
  - `src/agents/_shared/_prompts.py` loads `*.prompt` by stem.
- **operonx**
  - `PromptText` is a `str` (`operonx/prompts.py:95`) whose `{{x}}` is turned into `{x}` for `str.format` (`_as_template`, :199). `{{> other}}` is inlined at load (`_include`, :347), so the structure is gone before anything can show it.
  - `render` takes a prompt as a variable value (:133, `_plain` :179) and records it flat.
  - `LLMOp` fills strings with `str.format_map` (`providers/ops/llm.py:1796`).
  - The store keeps a version as `(project, name, version, text)` (`operonx/prompt_store.py:66`).

Root cause: operonx models a prompt as **text**, so every project composes prompts by
gluing text in code or through variables. Nothing can see the structure, because the
structure only ever exists inside Python.

## The model

### Objects

```yaml
# prompts/classifier.yaml — a composite
id: pr_c1a5
kind: chat                  # parts by role: what an LLM call takes
system:
  - pick: agent             # one link, chosen by a data value
    cases:
      educa_reminder: educa_reminder/analyzer
      educa_hr:       educa_hr/analyzer
      ahamove_hr:     ahamove_hr/analyzer
    else: none
  - var: intent_definitions # data from code
  - var: few_shot
  - use: classifier/rules   # a fixed link
user:
  - use: classifier/user
```

```markdown
<!-- prompts/classifier/rules.md — a text object -->
---
id: pr_9e02
---
CHỈ ĐƯỢC PHÉP chọn 1 intent nằm trong danh sách sau: {{allowed_intents_csv}}
```

| Part | Means |
|---|---|
| `use: <object>` | always link this object |
| `pick: <var>` + `cases` | link one object, chosen by the value of a data variable (`else: none` = nothing) |
| `when: <var>` + `use` | link it only when the variable is true (mr-finance's `voice`, `recount`) |
| `var: <name>` | data from code, placed as a whole part |
| `text: "…"` | a short literal between parts (rare; a separator or a heading) |

Rules that make keywords and variables tell themselves apart:

1. **Only text objects contain `{{x}}`, and `{{x}}` is always data.** There is no
   `{{> include}}` any more: links live only in composites.
2. **A variable can never hold a prompt.** Giving an object as a variable is an
   error that says "link it with `use`/`pick`".
3. **Choices are in the object, not in code.** `pick`/`when` make every branch
   visible. Code passes data (`agent="educa_hr"`, `voice=True`), never prompt text.
4. **No loops and no expressions.** A repeated block (rules 1..n, few-shot examples)
   is either a text object or data built by code.

### Identity

- `id` is minted when an object is created and never changes. Versions, labels,
  the store and run rows key on it, so renaming keeps history.
- `name` (the path) is what people and files write. The loader resolves names to
  ids. `operonx prompts mv old new` renames and rewrites the links that point at it.
- Each object has its own versions. A composite links to an object, and the label
  in use picks which version of it is served, so publishing a new
  `educa_hr/analyzer` doesn't need a new `classifier`.

### Using one

```python
CLASSIFY = prompt("classifier")            # a Prompt object, not a str

LLMOp(prompt=CLASSIFY)                     # the op fills it from its inputs at call time

msgs = CLASSIFY.render(agent="educa_hr", intent_definitions=…, few_shot=…,
                       allowed_intents_csv="…")
msgs.system, msgs.user                     # text, made only here
msgs.tree                                  # ids + versions of every linked object, and what pick chose
CLASSIFY.variables                         # {"agent": "pick", "allowed_intents_csv": "classifier/rules", …}
```

The run row records `msgs.tree` instead of today's flat refs. Details shows the root
as one chip, and opening it shows the tree with the branch `pick` took.

### Studio

- **Prompts page = a graph of objects.** A node is an object (text or composite); an
  edge is `use` / `pick` / `when` (a `pick` edge carries its case label). Click a text
  node to edit and version it.
- **Variables panel:** each variable with the object it comes from and its kind (`data` / `pick` / `when`).
- **Used by:** which composites link this object, and which ops call it.
- Same visual language as the flow canvas: plain wires, lavender, dark-first.

### Store

`operonx_prompt_versions` is keyed by `(project, id, version)` and gains `kind` and
`body` (JSON for a composite, text for a text object). Names live in their own
table. Labels key on `id`.

### Compatibility

- An existing `.md` prompt loads as a text object (an id is written into its front
  matter the first time). `prompt("x")` keeps working.
- For one minor, `PromptText` still behaves as a `str`, `{{> include}}` still
  inlines, and a prompt passed as a variable still works. All three warn. The next
  minor removes them, after the five projects below have moved.

## Phases

| # | Repo | Work | Gate |
|---|---|---|---|
| O0 | Operon (spike, no release) | Object model + loader + `render` for `use`/`pick`/`var`/`text`, enough for the callbot classifier. | The callbot's 28 golden prompts come out byte-identical from `prompts/classifier.yaml`. The owner OKs the file format |
| O1 | Operon | Everything above: `when`, ids + `mv`, versions/labels per id, store schema + migration, `LLMOp(prompt=<object>)`, run-row tree, `operonx prompts show` (prints the tree), lint (prompt in a variable, `.format`/`<<x>>`/`{x}` on prompt text, inline template with prose), guide. Deprecation warnings. | Existing prompt tests pass. New tests per part kind. 1.44.0 (PyPI after the owner says yes) |
| O2 | studio | Prompts graph, variables panel, used-by, Details tree. | `node --test tests/js/` green. Screenshots on the callbot classifier and mr-finance assistant. Own branch |
| O3 | callbot | `classifier.yaml` as above; rules out of `agent.yaml` into text objects; `_prompt.py` passes data only. | Golden byte-identical (28). Lint = 0. On refactor/operonx-studio |
| O4 | mr-finance | `assistant` composite: the 7 constants become text objects linked by `pick role` / `when voice` / `when recount` / `pick reads`. suggest/daily/copilot become objects. CIO becomes new objects through its two-person + eval flow. | Rendered instructions byte-identical for every role × switch fixture. Assistant eval unchanged. Lint = 0 |
| O5 | qc-snatcher, meeting-prep ×2 | `*.prompt`/constants become objects. `── DYNAMIC ──` becomes a `chat` object's system/user. `time_prompt=` becomes `pick`/`use`. | Byte-identical per prompt. Each project's tests pass. Its prompts are on the studio graph |
| O6 | Operon | Remove the deprecated `str` behaviour, `{{> include}}` and prompt-as-variable. | All five projects on the new minor, tests green |

Overall gate: no project builds prompt text in code (`grep` finds no `.format(` on
prompt text, no `<<x>>`, no `.prompt` loader). Lint = 0 everywhere. The studio
graph shows every project's prompts.

## Not in scope

- Changing what any prompt says: every move is byte-identical.
- Loops or expressions inside prompts.
- Agent `instructions=` callables that read run state (mr-finance `instructions(rctx)`)
  stay callables. They call `render` on an object with data, and no longer return
  glued text.
