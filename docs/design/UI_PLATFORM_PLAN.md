# Operonx Studio — Plan

`operonx-studio`: a web UI that manages operonx **projects** — resources,
env, dependencies, workflow graph, deploy target. Code is the source of
truth; the UI is a first-class projection of it, with a bounded write-back
path. Ships with `operonx-project`, the headless conventions + extraction
toolkit it reads through (§1.2).

**Status:** P0, P1, P2 complete; P3 config editing done. `operonx-project`
ships the manifest parser, `operonx-lint` (C3/C5/C6 statically, C1+C2 via
`--build`, plus `--suggest`), `operonx-extract`, and surgical config
editing — 130 tests.
`packages/operonx-studio` renders the IR to a self-contained page with
graph / resources / env / deps views and a live-reloading local daemon —
60 tests. All 16 tutorial examples carry an `operonx.toml`, lint clean,
extract deterministically, and render 16/16. **Callbot renders** — 6 graphs,
96 nodes — and lints with 0 errors. Next: typed graph edits.

---

## 1. The one idea

A codebase is UI-loadable if building its graph is **pure, cheap,
deterministic, and stably named**. Then the UI never parses Python — it
builds the graph in a sandbox and serialises the result to a **Project IR**.

    source  ──build()──▶  frozen GraphOp  ──serialise──▶  Project IR  ──▶  UI
      ▲                                                                    │
      └──────────── typed edits / assistant diffs ◀────────────────────────┘

The Project IR is **derived and disposable**. It is never committed. Git
carries source; the IR is a cache.

Everything in §2 exists to make that arrow work in both directions.

### Blast radius on the operonx package

**P0–P3 require zero changes to `operonx/`.** This is a design constraint,
not an accident. Verified:

| Need | Where it lands |
|------|----------------|
| Extractor reads `full_name`, `_edges`, `_back_edges`, `_rewritten_from`, `_loop_mode`, edge `soft`/`pinned_hard`, `ResourceHub` | **pure reads** — no change |
| C2 lazy resource acquisition | **already true** in operonx (`_ensure_initialized`); only project code violates it |
| `operonx-lint` / `operonx-extract` CLI | same new package |
| Multi-project vs `ResourceHub._instance` singleton | **worked around**, not changed — one process per project (P4) |
| S9 (`hasattr` on a `Ref` fabricates a `Ref`) | extractor-side: read via `object.__getattribute__` against a type whitelist |

Two findings would eventually argue for a core change, and both are
deferrable and separately decidable:

- **S1** — an op exception is swallowed (`base.py:1021`), so a run view
  would paint a node green when the work failed. Blocks **P4 only**.
- **S7** — Ref-vs-Ref in `if_()` silently takes the first branch. **P3 can
  forbid it in the linter instead of fixing core.**

Neither is a prerequisite for shipping the viewer.

### 1.2 Packaging — two distributions

Both follow the existing `packages/operonx-code` **layout**: a distribution
under `packages/`, hatchling backend, Apache-2.0, `requires-python >=3.10`,
versioned independently of core, hyphenated CLI entry points (matching
`operonx-pack` and `operonx-code`).

They must **not** inherit its dependency line. `operonx-code` declares
`operonx[standard]`, which pulls fastapi, uvicorn, websockets, openai,
langfuse, aiohttp and numpy — a full web stack in every CI lint run, the
exact cost the split above exists to avoid. Instead:

```toml
# operonx-project
dependencies = ["operonx>=1.0.0", "tomli>=2.0; python_version < '3.11'"]
```

`operonx-project` runs **inside the target project's own environment** — it
has to, in order to import that project's code — so it inherits whatever the
project already installs and must impose nothing of its own. AST work is
stdlib. `operonx-studio` carries the web stack alone.

Note the repo has no `[tool.uv.workspace]`, which is why `operonx-code`'s
tests need `PYTHONPATH=packages/operonx-code`. That is a wart, not a
convention — declare a workspace or use an editable install rather than
replicating it.

| Package | Owns | Dependencies |
|---------|------|--------------|
| **`operonx-project`** | `operonx.toml` manifest, the C1–C7 conventions, `operonx-lint`, `operonx-extract`, the Project IR schema | pure Python + `operonx` |
| **`operonx-studio`** | local daemon, web UI, typed write-back, run + inspect | heavy (web stack); depends on `operonx-project` |

**Why two and not one.** The linter and extractor run in CI and pre-commit
for every project, callbot included. If they ship in the same distribution
as the web UI, checking a naming convention pulls a web framework into every
CI run. Projects depend on `operonx-project` only; `operonx-studio` is a
developer tool installed once per machine.

Deliberately **not** named after n8n: that is another company's trademark, it
anchors expectations to drag-and-drop — which this explicitly is not — and it
dates the product to a competitor.

---

## 2. Project conventions — normative

Six rules, each with a tool that consumes it. Two more were drafted and
deleted — see the note at the end of this section.

Each rule states the **rule**, the **why**, and the **check** that enforces
it. All checks ship as `operonx-lint`, runnable in CI and by the UI before
load. A project passing `operonx-lint` is guaranteed loadable.

### C1 — Every project has a manifest

`operonx.toml` at the project root:

```toml
[project]
name = "callbot"
src  = ["src", "."]                  # import roots; defaults to ["."]

[resources]
base    = "~/.operonx/common.yaml"   # optional shared hub
overlay = "resources.yaml"           # project-specific; merged over base

[[graph]]
name   = "callbot.ahamove_hr"                        # project-local label
entry  = "callbot.graph:build_ws_callbot_pipeline"   # import path
[graph.bind]
agent  = "agents.ahamove_hr:agent"                   # used as found
```

`src` matters whenever packages do not sit at the project root — callbot
keeps them under `src/` and declares `pythonpath = ["src", "."]` for pytest.
Without the same list, nothing imports.

**Why.** 31% of callbot's ops are produced by factories taking injected
dependencies. Without a declared binding the UI cannot construct the graph
at all — the nodes do not exist until something is injected.

Three properties this format needs, each learned from real code:

- **It curates entries; it is not an inventory.** Ops and factories are
  never listed — callbot's 12 factories appear in no manifest. They are
  discovered when the entry is built. Sub-graphs are found by walking the
  built graph and rendered as nested boxes; list one as an entry only if you
  want to load it standalone. Otherwise a large project's manifest becomes a
  junk drawer.
- **`name` is a project-local label, `entry` is the import path, and they
  need not match.** Callbot defines `workflow` in two packages and
  `build_mock_chat_pipeline` in two more; labels disambiguate. This is a
  different namespace from C3, which governs op names *inside* a graph.
- **`bind` names an object, used exactly as found — never called.** An
  earlier draft required a zero-argument provider. That is ambiguous the
  moment a dependency is *itself* callable: callbot's
  `build_mock_chat_pipeline(agent, sink_op)` takes an op **as a value**, and
  invoking it would inject the wrong thing entirely. Projects needing
  construction expose a module-level instance, which callbot already does
  (`agents.ahamove_hr:agent`) — so this convention cost it no new code.

A useful side effect: because one builder plus N bindings yields N entries,
the manifest becomes the explicit **catalogue of deployable variants**.
Callbot has four agents behind one `build_ws_callbot_pipeline`; today that
list exists only implicitly in `ws_server.py` routing and env vars.

**Project boundary.** One manifest per *environment*, not per package. A
project is a unit sharing one venv, one `.env`, and one resource hub — which
is forced, not chosen: `ResourceHub._instance` is a class-level singleton,
and top-level module names collide across projects (every tutorial example
defines `main`, and resolving two in one interpreter silently returns the
first project's module). **One process, one project.** Callbot spans three
packages and gets exactly one `operonx.toml` at its root.

**Check.** Manifest parses; every `entry` and `bind` target imports; no
foreign module of the same name is already loaded.

### C2 — Construction is pure and cheap

`__init__` of any op MUST NOT load a model, open a socket, read a large
file, or call an API. Resource acquisition is lazy (first use) or goes
through `ResourceHub`.

The same applies at **module scope** and to **`bind` providers**. Extracting
one graph from a project whose graphs are scattered across packages —
callbot imports five modules across three packages — pays every module's
import cost on every UI load.

**Why.** The UI builds the graph every time it loads a project.

This rule **codifies operonx's existing pattern rather than demanding a new
one**: every provider op (`LLMOp`, `EmbeddingOp`, `RerankOp`,
`VectorSearchOp`, `DocFetchOp`) already defers `hub.get(...)` to
`_ensure_initialized()`. Nothing in operonx acquires resources in `__init__`.

The violations are in project code. Callbot's `DenoiseClassifier.__init__`
calls `_get_denoise_session(model_path)` — drawing a diagram would load ONNX
weights.

**Check.** `operonx-lint --build` constructs every declared graph with the
network blocked — `connect`, `connect_ex`, `create_connection` and
`getaddrinfo`, since resolving a name already makes the network a build-time
dependency. Loopback is allowed: a local sidecar is not the dependency this
rule is about. Reaching out fails the check and names the address.

Duration is **reported, not enforced** — a threshold that fails on a loaded
CI box is a flaky test. Builds over a second are flagged, which is three
orders of magnitude above a graph that constructs nothing heavy.

Callbot shows why the *first* measurement is the only honest one:

```
callbot.ahamove_hr      built offline in 2241ms   [C2] slow
callbot.educa_reminder  built offline in  257ms
asr_flow                built offline in    2ms
```

`_denoise_session` is a module-level global, so the ONNX load is paid by
whichever graph builds first and is free afterwards. Measure only the
steady state and the violation is invisible.

### C3 — Every op has a stable, unique name

A name is stable if it comes from (a) explicit `name=`, (b) a clean
single-target assignment (`stt = HttpSttOp(...)`), or (c) the decorated
function name. It is NOT stable if `auto_name()` falls through to
`unique_name()` — a per-process UUID.

Names MUST be unique within a graph.

**Why.** Node identity is `parent.full_name + "." + name`. It is the key
for layout, comments, run history, and diffs. A UUID id changes every
process; a duplicate name **silently overwrites** the earlier op
(`graph_op.py:201`).

**Check.** Static: flag op construction in any position `_parse_assignment`
rejects (tuple unpack, argument, dict value, comprehension, attribute
target) without `name=`. Dynamic: after build, assert no op name matches
`^[0-9a-f]{8}$` and no duplicate warning was emitted.

### C5 — Wiring is declarative

A `@graph` body contains only: op construction, `>>` / `~` wiring, `if_()`
branches, and Ref expressions. No `for`/`while`/comprehension that creates
or connects ops. No conditional wiring on runtime values.

**Why.** This is what makes UI edits expressible as source edits. A loop
that builds nodes has no stable mapping back from a diagram.

**Check.** AST: no op construction or `>>` inside a loop or comprehension
within a `@graph` body. Looping over a *literal* of already-built ops is a
warning — extraction is unaffected; only in-place editing is constrained.

### C6 — Resources by key only

Ops reference resources by string key (`resource="gpt-4o"`). Never
construct a provider inline. Never read credentials in project code.

**Why.** Enables the base + overlay merge in C1 and lets the UI swap a
resource without touching Python.

**Check.** AST: no provider class instantiated in project code; every
`resource=` value resolves in the merged hub.

### C7 — The env contract is derived, never hand-maintained

Every secret enters through `${VAR}` in `resources.yaml`.
`${VAR}` = required, `${VAR:default}` = optional.

**Why.** The UI generates the `.env` form, and the required-key list, by
scanning resources.yaml. One source of truth for "what secrets does this
project need".

**Check.** No `os.getenv` for credentials in project code.

### Two rules that were deleted

**C4 (`@op_factory` required)** and **C8 (`@opaque` required)** were drafted
here and removed once their consumers were audited: there were none.
`is_op_factory()` and `opaque_reason()` had no callers, the extractor
consulted neither, and `--suggest` finds builders structurally — *a function
containing a nested `@op`* is a fact the AST reads directly.

C4's own message claimed "no tool can discover it" while the discovering
tool did not use it. C8 was never implemented at all, yet C5 advised marking
a region `@opaque` — advice pointing at a decorator that did nothing, the
same shape as the S12 install hints that sent users to a non-existent extra.

Enforcing C4 would have cost callbot 17 edits across modules that ship in
Docker, and required new public API in operonx core so a dev tool would not
become a runtime dependency of a deployed service. All of that to satisfy a
rule whose only customer was itself.

**The test a convention has to pass: name the tool that consumes it.**
Neither could.

### Convention summary

| Rule | One line | Blocks |
|------|----------|--------|
| C1 | manifest declares graphs + injections | discovery |
| C2 | construction is pure and cheap | load speed, offline load |
| C3 | stable unique names | identity, layout, diffs |
| C5 | wiring is declarative | UI → code writes |
| C6 | resources by key | resource editing |
| C7 | env derived from resources.yaml | secrets UI |

---

## 3. Project IR

Derived by building each declared graph in a sandbox and walking the frozen
`GraphOp`. JSON, versioned, never committed.

```jsonc
{
  "ir_version": 1,
  "project": "callbot",
  "graphs": [{
    "name": "ws_callbot_pipeline",
    "nodes": [{
      "id": "ws_callbot_pipeline.stt",     // full_name — the stable key
      "kind": "HttpSttOp",
      "bound": "io",
      "resource": null,
      "inputs":  [{"name": "speech_audio", "from": "...stt_input.speech_audio"}],
      "outputs": [{"name": "transcript"}],
      "source": {"file": "src/callbot/graph.py", "line": 117}
    }],
    "edges": [{
      "from": "...stt_input", "to": "...stt",
      "type": "normal", "soft": false, "origin": "authored"
    }],
    "loops": [{
      "id": "...__loop_0__", "mode": "synthetic",
      "body": ["..."], "entry": "...", "exits": [["...","..."]],
      "back_edges": [["...","..."]],       // from _rewritten_from
      "until": null
    }]
  }],
  "resources": { "...": "merged base + overlay, values redacted" },
  "env": { "required": ["OPENAI_API_KEY"], "optional": {"LOG_LEVEL": "INFO"} }
}
```

Three details that are not optional:

- **`edge.origin`** distinguishes `authored` / `auto_soft` / `pinned_hard`.
  The build rewrites edges; the UI must show what you wrote, not only what
  the compiler produced.
- **`loops[].back_edges`** comes from `_rewritten_from`, the audit dict with
  zero runtime readers. Cycle rewriting *deletes* back-edges from `_edges`,
  so this is the only way to redraw the source-level cycle. Do not remove it.
- **Three loop kinds render differently.** Synthetic loops have `until: null`
  — their exit condition lives in an `if_()` inside the body. A node
  inspector must point into the body, not show a condition field.

**Layout lives in `.operonx/layout.json`**, keyed by node id, sorted, one
node per line. Never in Python — otherwise every drag is a source diff.
Auto-layout by default; store only pinned positions.

---

## 4. Architecture — local-first

The builder runs as a **local daemon** on the developer's machine or their
own server. The browser UI talks to it over localhost.

**Why not server-side.** Extraction *executes project code* (C1/C4). A
hosted extractor is a code-execution sandbox holding customer source and
secrets — a security product on the critical path of the viewer. Locally,
it is just running your own code.

Consequences, all favourable:

- git auth = the user's existing ssh keys / credential helper. **Zero
  credentials stored.**
- `.env` and source never leave the machine.
- **Multi-user is git.** No tenancy layer in v1, and it is consistent with
  "code is the source of truth". Merge behaviour *is* the collaboration
  model — which is why §5 P1 makes round-trip diff-cleanliness a gate.
- Hosted multi-tenant later reuses the same daemon protocol, informed by
  what actually needed isolating.

Forge auth, when we get there: own identity + per-forge linked credentials,
**not** "log in with GitHub". A project on self-hosted GitLab needs separate
credentials regardless, so a user table exists either way; login-with-git
buys little and couples account lifecycle to a third party. Prefer
App-installation tokens (short-lived, per-repo, revocable) over stored PATs.

**Write path, two tiers.** Deterministic typed edits for the structural
vocabulary — insert op, rewire edge, toggle soft/hard, rename, change
resource, edit resources/env/deps. Assistant-generated diffs for anything
outside it, always human-reviewed. The UI never regenerates a Python file
wholesale, so comments and formatting survive. Config files
(`resources.yaml`, `.env`, `pyproject.toml`) *are* fully generated — they
are UI-owned.

---

## 5. Phases

**P0 — `operonx-project`: conventions + linter.**

- ~~Scaffold the package~~ — done, `packages/operonx-project`.
- ~~Manifest spec and parser~~ — done, `Manifest.load()`, 31 tests.
- ~~`@op_factory` / `@opaque` markers~~ — built, then **removed**: nothing
  consumed them (see §2).
- ~~Convert the tutorial as the reference project~~ — done, 15 manifests,
  48 graphs, every entry verified to resolve.
- ~~`operonx-lint` for C3–C6, plus `--suggest`~~ — done. C1 is checked at
  the CLI level; C2 needs a build and lands with P1.

Two rules were too blunt on first contact and the tutorial caught both: C3
was flagging throwaway root instances in `main()` (scoped to `@graph` bodies),
and C5 was erroring on `for leaf in (a, b): leaf >> PARENT` where the topology
is still static (now a warning, since only the write path is constrained).

*Exit:* met on the tutorial. Its report on callbot is the migration worklist.

**P1 — `operonx-project`: extractor + determinism gate.** ~~Build → Project
IR (`operonx-extract`)~~ — done. *Gate met:* extracting any project twice in
fresh processes yields byte-identical JSON; 16/16 examples pass. No object
ids, no timestamps, keys sorted.

Two things the build-out settled that the plan had only assumed:

- **`Ref._source` holds the producing op object, not its name** — its
  `repr` renders the full name, which is easy to mistake for the stored
  value. Read `full_name` off the op; never probe the `Ref` (S9).
- **A node needs two anchors, not one.** `defined_at` (from
  `code_fn.__code__`) is where an op's body lives; `wired_at` (from the AST)
  is where it was constructed. A class-based op such as `LLMOp` or
  `InterruptOp` has *only* the latter, because its class lives in operonx.
  This is the AST-for-anchors / build-for-semantics split paying off.

The full source→IR→source round-trip gate moves to P3, where a write path
first exists.

**P2 — `operonx-studio`: read-only UI.**

- ~~Graph viewer~~ — done. Layered layout computed in Python, not the
  browser: the same IR always draws the same picture, so a diff means
  something and it is testable without a headless browser.
- ~~Inspector showing where each input comes from~~ — done.
- ~~Resources, env-contract and dependency views~~ — done.
- ~~Local daemon with live reload~~ — done, web stack behind the `serve`
  extra so rendering a graph never pulls a server. Editing is P3.

**The daemon extracts in a subprocess, never in its own process.** Not
defensive — required. Extraction imports the project's modules, so
re-extracting in the same interpreter returns the *cached* module and serves
stale structure while claiming to be live. Verified end to end: adding an op
to a served project moved the IR from 10 nodes to 11, which an in-process
reload could not have done. A project that fails to import shows its
traceback in the page and recovers on its own.

*Exit: met.* Callbot renders — 6 graphs, 96 nodes, 20-layer pipeline from
`source` through `route_1 → asr → denoise → picker → route_2 → decider → …`
to `tts`. 8 auto-softened edges are distinguished from 19 authored ones, and
20 of 24 nodes carry a source anchor. The 4 without are `BranchOp`s from
inline `if_()` calls, which produce no assignment to anchor to — correctly
reported as absent rather than guessed.

**Machine state stays out of the IR.** Whether *this* box satisfies a
project's env contract is computed at render time and injected, never
extracted: the IR is gated on being byte-identical across runs, and folding
in environment state would make two extractions of the same commit disagree
on different machines.

**No value is ever read or shown** — only whether a name is present, and
whether from the environment or `.env`. Those fail differently: a name in
`.env` is useless to a project that never loads it. A rendered page is a
file that gets shared, so a rendered secret is a leaked secret.

Three things the viewer had to get right, each taken from a way an earlier
attempt failed:

- **Every node is drawn**, orphans included. A diagram that quietly omits
  things is worse than none.
- **No edge comes from nowhere.** `START`/`END` are boundaries drawn as pins
  on nodes, never invented as phantom nodes.
- **The inspector shows real data** — each input names its producing op and
  output, its `SCRATCH` key, or its literal. "What feeds this?" is the
  question a graph is opened to answer.

Cycles are excluded from layering: a cyclic edge has no forward layer to
advance to, and longest-path over one does not terminate. Rewritten cycles
are announced from the rewrite record rather than silently vanishing.

*Exit: met.* Callbot renders — 6 graphs, 96 nodes, 20-layer pipeline from
`source` through `route_1 → asr → denoise → picker → route_2 → decider → …`
to `tts`. 8 auto-softened edges are distinguished from 19 authored ones, and
20 of 24 nodes carry a source anchor. The 4 without are `BranchOp`s from
inline `if_()` calls, which produce no assignment to anchor to — correctly
reported as absent rather than guessed.

**P3 — the write path.**

- ~~Config editing~~ — done, in `operonx-project` (it owns these formats;
  studio's "typed write-back" is the *graph* edits). Not codegen: see §4.
  Gate met — **10/10 resource files and 11/12 env files are byte-identical
  under a no-op edit.** The twelfth is not a tool failure: callbot's
  `.env_staging` assigns `VAD_NEED_PADDING` twice with different values, so
  "set it to its own value" has no single answer.
- ~~Typed graph edits~~ — `rename_op` and `set_op_resource`, same rule:
  **locate with the AST, splice the text.** Never parse-and-unparse —
  `ast.unparse` emits valid Python and destroys every comment, alignment and
  quoting choice, so a UI that "moved a node" would return a file its author
  no longer recognises. The AST is used only for what text cannot answer:
  which `pre` is the variable and which is inside a string or an attribute.

  Gate met on real code — **21 files, 57 graphs, 183 ops** rename to
  themselves byte-identically, and **29 literal `resource=` sites** re-set
  to their own value with no diff. A computed `resource=agent.llm_resource`
  is deliberately **refused**: rewriting it would sever the injection the
  author chose (C6).

  End to end on ex16 — rename `hits` and repoint `answer` — is 4 changed
  lines, and the rebuilt IR shows the new name, the rewired edges and the
  new resource:

  ```
  -    hits = VectorSearchOp.of(          +    search_hits = VectorSearchOp.of(
  -        ids=hits["ids"],               +        ids=search_hits["ids"],
  -        resource="gpt-4o-mini",        +        resource="gpt-4o",
  -    START >> q_emb >> hits >> ...      +    START >> q_emb >> search_hits >> ...
  ```

  Quote style follows the file: `repr` always single-quotes, so re-emitting
  an unchanged `resource="gpt-4o"` as `'gpt-4o'` would rewrite a line that
  did not change.

- ~~Insert and delete~~ — `insert_op_between`, `insert_op_after`,
  `delete_op`. Wiring is edited by flattening a `>>` chain into its operand
  *source spans*, changing the list, and rejoining — so `gate >> [ex, gd,
  av, fl]` survives verbatim instead of being reformatted.

  Gates met on real code:

  | round-trip | result |
  |---|---|
  | insert-after + delete, every op | **183/183 byte-identical** |
  | insert-between + delete, every real edge | **109/109 byte-identical** |

  `delete_op` **refuses** when another op still reads the target: deleting a
  depended-on node does not make a smaller graph, it makes a broken one, and
  the honest answer is "disconnect it first". A wiring statement left with
  fewer than two ends is dropped rather than left dangling.

  Building this exposed that `insert_op_after` is the *coarse* form. In
  ex06, inserting after `a` landed in the `EmitOp` branch as well as the
  main one — surprising when the user clicked one edge. Hence
  `insert_op_between`, which is what a "+" on an edge should call; the
  coarse form is kept and documented as affecting every chain.

- ~~Edit API~~ — `plan_edit` / `apply_plan` bridge the UI's vocabulary
  (manifest label + short node name) to a file on disk, and the daemon
  exposes it at `POST /api/edit`.

  **Dry run is the default.** A request that omits the flag previews rather
  than writes, and the response always carries the unified diff. That is
  what "code is the source of truth" has to mean in practice: the file
  changes only after someone has seen exactly how.

  Applying writes the file; the watcher notices on its next poll and the
  page reloads itself, so there is no second refresh path to keep in step.
  Verified end to end against a live daemon — preview left the file
  untouched, apply wrote it, and the next `/api/ir` showed the renamed node.

  Two safeguards worth naming:

  - **Stale-plan detection.** `apply_plan` refuses when the file changed
    between planning and applying. The daemon reloads on every save and a
    user editing in their own editor at the same time is normal, not exotic.
  - **A closed vocabulary.** `ACTIONS` is a fixed map; a UI cannot invent an
    action the tools cannot verify. Anything outside it is an assistant
    diff, reviewed as a diff.

  Addressing needed one fix: a manifest entry names the **builder**
  (`callbot.graph:build_ws_callbot_pipeline`) while the body belongs to the
  `@graph` inside it, so `find_graph` now resolves either — otherwise the
  edit API would find nothing in exactly the projects that use the pattern.

- ~~Edit affordances in the page~~ — the node inspector gains rename, set
  resource and delete. Clicking one **previews**: the diff renders in the
  panel with added and removed lines coloured, and only then is there an
  "apply to <file>" button. After applying, the watcher sees the write and
  the page reloads itself.

  **The controls are gated on being served.** A file written by
  `operonx-studio PATH` may be shared or committed, and it must neither poll
  a daemon that is not there nor offer buttons that call an API it cannot
  reach — so the daemon injects `__OPERONX_EDITABLE__` alongside the reload
  hook, and the static page only ever *reads* the flag. The error page does
  not set it either: there is nothing to edit against when the project will
  not build.

  Refusals surface as text rather than silence. Deleting a depended-on node
  answers *"'hits' is still read by another op; disconnect it before
  deleting"*.

- ~~Scaffolder~~ — `operonx-new [PATH] [--llm]` writes a project that
  already satisfies the conventions. Its acceptance test is the project's
  **own tools**, not string assertions: the generated project lints with
  zero findings, builds offline, extracts, renders, and runs.

  ```
  operonx-new demo && cd demo
  operonx-lint --build .     -> flow  built offline in 353ms
  operonx-studio .           -> 1 graph(s), 2 node(s)
  ```

  It emits `pyproject.toml`, not `requirements.txt` — uv and every tutorial
  example use the former. The operonx floor is the tutorial's `>=1.3.0`
  rather than `>=1.0`: 1.0.0 was a breaking release, so a lower floor would
  let a fresh project resolve to an API the generated code does not speak.
  It never clobbers, and names what is in the way when it refuses.

  `--llm` adds an `LLMOp`, `resources.yaml` and `.env.example`, and takes
  the `operonx[openai]` extra.

- **P3 exit: met.** A project can be created, viewed, edited and run without
  leaving the tooling.

Four format features surfaced only by running against files that ship, each
now a regression test:

| feature | what naive handling did |
|---|---|
| trailing `# comment` in `.env` | swallowed into the value and re-quoted |
| `NAME = value` spacing | leading space treated as part of the value |
| `api_version: "2025-04"` | quotes dropped; YAML may not read it back as a string |
| duplicate keys | only the first updated, so the effective value never changed |

*Exit:* create a working project end-to-end in the UI.

**P4 — `operonx-studio`: runtime.** One process per project — `ResourceHub._instance` is a
class-level singleton, so two projects cannot be live in one process. Run,
stream logs, inspect state per context. *Exit:* run callbot from the UI.

**P5 — Deploy + hosted.** Git integration, deploy targets, then multi-tenant
if still wanted.

---

## 6. Callbot migration

Smaller than feared. The injection pattern **survives** — it does not need
restructuring.

| Work | Size |
|------|------|
| ~~One `operonx.toml` at the repo root~~ | **done** — 6 graphs |
| ~~Providers for each injected agent~~ | **not needed** — see below |
| Move resource acquisition out of `__init__` (C2) — `DenoiseClassifier`, audit `HttpSttOp` and others | **the real work** |
| Verify no duplicate op names across factory calls (C3) | audit |
| Confirm 27 module-scope ops keep stable names | already passing |

Callbot's graphs are scattered across three packages (`callbot`, `agents`,
`speech`) with colliding symbol names — `workflow` in two packages,
`build_mock_chat_pipeline` in two more. None of that needs restructuring:
labels disambiguate, and the manifest never enumerates the scattered
factories. Four agents behind one `build_ws_callbot_pipeline` become four
entries sharing an `entry` and differing only in `bind`.

Two conventions were **wrong, and callbot was right** — corrected in the
toolkit rather than forced onto the project:

- **`bind` names an object, used as-is — never called.** The earlier
  "zero-argument provider" rule is ambiguous the moment a dependency is
  itself callable: `build_mock_chat_pipeline(agent, sink_op)` takes an op
  *as a value*, and calling it would inject the wrong thing. Callbot already
  exposes module-level singletons (`agents.ahamove_hr:agent`), which is
  unambiguous and needs no new code.
- **`resource=` need not be a literal.** `resource=agent.llm_resource` is
  deliberate injection that keeps `graph.py` agent-agnostic. Extraction
  resolves it, so it is now a warning about the *write* path, not an error.

Two real gaps in the toolkit, both invisible until callbot:

- **`[project] src`** — packages need not sit at the project root. Callbot
  declares `pythonpath = ["src", "."]`; without the same list nothing
  imports.
- **Anchors keyed by source root and by builder name.** Two separate
  silent-miss bugs: `src/callbot/graph.py` was keyed `src.callbot.graph`
  while the entry says `callbot.graph`, and a builder entry names
  `build_ws_callbot_pipeline` while the body belongs to the `@graph` inside
  it. Fixing both took callbot from 16/24 anchors to 20/24 and the workflows
  to 12/12 and 10/10.

**Nothing remaining.** The 17 `@op_factory` findings were dissolved rather
than fixed — see §7. Callbot lints clean: 0 errors, 22 warnings (3 injected
`resource=`, 17 advisory C4, 2 credentials read outside the resource file).

---

## 7. Risks

- **Known core findings become UI-visible.** S1 (op exception swallowed at
  `base.py:1021`) means a run view paints a node green when the work failed
  — P4 is blocked on it. S7 (Ref-vs-Ref in `if_()` silently takes the first
  branch) means a UI that lets you draw `if a == b` is a bug factory — P3
  must either forbid it or S7 must be fixed. S9 (`hasattr` on a `Ref`
  fabricates a `Ref`) will bite any introspecting extractor — P1 must read
  fields via `object.__getattribute__` against a type whitelist.
- **Scope.** Builder + scaffolder + package manager + secret manager + git
  client + deploy orchestrator is five products. The phase order above ships
  value at P2 and defers all five.
- ~~The markers are in the wrong package.~~ **Resolved by deleting the
  requirement, not by moving the marker.** Making `@op_factory` mandatory
  would have forced an unpublished dev tool to become a runtime dependency
  of a deployed service. Auditing what consumed it showed nothing did, so C4
  became advisory (see §2) and the problem disappeared: callbot now lints
  with **0 errors**, and the tutorial example that had taken a path
  dependency on `operonx-project` gave it back.

  The lesson generalises: a convention that costs real edits in real
  projects has to name the tool that consumes it. C4 could not.
