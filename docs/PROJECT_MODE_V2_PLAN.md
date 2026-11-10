---
title: Project mode v2 — one board, cards from units
status: open
phases:
  - {id: V1, title: Unit templates and operonx add, status: open}
  - {id: V2, title: The card format, status: open}
  - {id: V3, title: The project page is board plus chat, status: open}
  - {id: V4, title: The assistant works card by card, status: open}
  - {id: V5, title: The engine cut, status: open}
  - {id: V6, title: Gate on a real task, status: open}
updated: 2026-11-10
---

# Project mode v2 — one board, cards from units (2026-11-10)

**Asked** (owner). Project mode is over-built. Keep a Jira-like board. Break a task into cards made from
ready units; what no unit covers is *flexible*. Separate the layers. Templates only for patterns that are
really reused. The running card is highlighted and shows its progress. No decoration.

## 1. Why — measured 2026-11-10

```
assistant turns (all projects)        90
  tool calls: Bash 166 · Read 38 · project_status 2 · failing_on_base 1
  turns tied to a board card           0          ← real work happens in chat, not on the board

builder on tipcalc (only real run)    35 send-backs
  held-out tests (QA)                 16   ← 4 of 6 "ask a human" came from held-out loops
  needs-human questions               10
  review findings                      5
  project checks                       4
  held-out test itself rewritten       2   ← the test was wrong, not the code

code size        engine 14.5k lines (brd 2.1k · flows 2.0k · coder 0.7k · telegram 0.7k)
                 studio project mode ~2k lines JS (projectmode.js, board.js)
```

## 2. Architecture

```
┌─ Studio · project page ─────────────────────────────────────────────────────┐
│                                                                             │
│   BOARD  (lanes × status)                   │   CHAT  (the assistant)       │
│   ▶ the running card, highlighted           │   task → cards → work         │
│     its live progress line                  │                               │
│                                             │                               │
└───────────────┬─────────────────────────────┴───────────────┬───────────────┘
                │ reads, re-renders on change                 │ one Claude Code turn
                ▼                                             ▼
   docs/brd/board.md   ◄── card status, card id ────   assistant (MCP: card_set)
   docs/brd/requirements.md  ◄── req ids                      │
                                                              │ operonx add <unit>
                                                              ▼
                              operonx/cli/templates/_units/  ──►  project code + its test
                                                              │
                              runhook progress events ────────┘──► the running card's line
```

Two files and one catalog are the whole model:

| | holds | changes |
|---|---|---|
| `docs/brd/requirements.md` | what and why: one row per requirement, its source, its test | rarely |
| `docs/brd/board.md` | how and how far: the cards | every turn |
| `_units/` (in operonx) | the ready units, each a template plus its test | when a pattern earns it |

## 3. Lanes — clean architecture rings

```
          ┌──────────────────────── platform ────────────────────────┐
          │  operonx.toml · resources.yaml · env · deploy · docker   │
          │                                                          │
          │   UI/UX ──► web backend ──► core: flow ──► core: domain   │
          │   web/pages   app/main.py      graph.py      rules, model │
          │               bodies.py        ops.py        (no I/O)     │
          │               jobs               │                       │
          │                                  ▼  only via ops + resources
          │                          data          AI                │
          │                          store.py      LLM op + prompt   │
          │                          sources       agent + tools     │
          └──────────────────────────────────────────────────────────┘

   arrows = "may import";  domain imports nothing;  tests are not a lane — every card brings its own
```

| lane | folders | units |
|---|---|---|
| UI/UX | `web/pages`, `web/lib` | page |
| web backend | `app/main.py`, `src/<f>/bodies.py` | route, job |
| core | `src/<f>/graph.py`, `ops.py`, `rules.py`, `model.py` | flow, op, rule |
| data | `src/<f>/store.py`, sources | store, source |
| AI | `src/<f>/prompts.py`, `agent.py`, `tools.py` | llm, agent |
| platform | `operonx.toml`, `resources.yaml`, deploy files | — (always flexible) |

## 4. Unit catalog

Admission rule: **≥ 3 uses in ≥ 2 projects, or ≥ 5 in one.** Below that, a pattern stays flexible.

| unit | lane | stamps | real uses (6 projects) | template from |
|---|---|---|---|---|
| op | core | `@op` in `ops.py` + unit test | 336 `@op` | `templates/hello` |
| flow | core | `graph.py` + run test | 91 `@graph` | `templates/hello` |
| rule | core | pure module + test | 17 files | tipcalc `money.py` |
| route | web backend | `Service(...)` + `bodies.py` + HTTP test | 93 `Service` | `templates/http`, mr-finance `news/bodies.py` |
| job | web backend | `Job(...)` + run test | 40 `Job` | mr-finance `app/main.py` |
| store | data | `store.py` (SQLite) + test | 15 files | mr-finance `news/store.py` |
| source | data | fetch → normalise → store + fixture test | 5 (mr-finance) | mr-finance `market/sources.py` |
| llm | AI | `LLMOp` + prompt + fake-model test | 68 refs | `templates/chat` |
| agent | AI | `AgentOp` + tools + fake-model test | 30 refs | `templates/agent` |
| page | UI/UX | `web/pages/<x>.js` + UI test | 39 (mr-finance) | a small mr-finance page |

Coverage: **49 %** of the 477 code files across the six projects already have one of these shapes (tipcalc 85 %,
qc-snatcher 59 %, meeting-prep 57–60 %, mr-finance 47 %, callbot 32 %). The rest is real project code: audio
(wav/vad/stt/tts), valuation, signing, retention. It stays flexible.

Not admitted (seen once or twice): `judge.py`, `mcp.py`, `golden.py`, `format.py`, `wav.py`.

## 5. The card and the board

```
### C4 · threshold rule · doing
lane: core · unit: rule · req: NEWS-07 · after: C2
files: src/news/rules.py, tests/news/test_rules.py
done: tests/news/test_rules.py::test_gold_jump, project tests green
```

```
 lane        │ todo            │ doing                          │ needs you          │ done
─────────────┼─────────────────┼────────────────────────────────┼────────────────────┼──────────
 UI/UX       │ C6 alert tile   │                                │                    │
 web backend │ C5 /api/alerts  │                                │                    │
 core        │                 │ ▶ C4 threshold rule    4m12s   │ C3 engine (flex)   │ C1
             │                 │   pytest 812/1220 · ✓ rules    │   review the diff  │
 data        │                 │                                │                    │ C2
 AI          │                 │                                │                    │
```

```
 status:   todo ──► doing ──► done
                      │  ▲
                      ▼  │
                   needs you        (a question, or a flexible card's review)
```

- One card = one lane = one unit (or flexible). A task across lanes becomes several cards.
- Only the assistant moves cards. The owner answers what is in *needs you*.
- The running card shows the running command's last progress line and its elapsed time. A line older than
  30 s shows its age, so a slow step is never silent.

## 6. One task, end to end

```
owner:  "alert when gold moves > 2 % in a day"
   │
   ▼  breakdown turn ─────────────►  C1 store   C2 source   C3 engine (flex)   C4 rule   C5 route   C6 page
   │                                  (todo on the board; the owner edits or says go)
   ▼  per card, in `after:` order
        ready    operonx add rule news threshold  → fill the body → its test → project tests → done
        flexible a 3-line design note on the card → code → tests → needs you → done
   │
   ▼  every status change is a card_set call in the turn; the stop hook refuses a turn that ends
      with a card still `doing`
```

Checks: the card's `done:` tests plus the project's tests. Review only for flexible cards. **No QA stage, no
held-out tests.**

## 7. Keep and cut

```
KEEP                                          CUT
────────────────────────────────────────      ──────────────────────────────────────────────
board store + reader (card format v2)         discover / plan / final flows
brd check, slimmed to requirements.md         review → held-out → QA stages, examiner
status (one read-only summary)                practices.toml, retro, LESSONS
studio: turn checks, stop hook,               plan status blocks → milestone mirror
        failing_on_base, live progress        studio pages: Checklist · Questions · Changes · Graph
operonx init templates (+ _units)             decorative animation on the project page (hand.js)
```

Before each cut: grep the second job (`feedback: check the second job before removing an API`). The
Telegram bridge (0.7k lines) stays until the owner says whether they use it from the phone.

## 8. Phases

| | what | gate |
|---|---|---|
| V1 | `_units/` with the 10 templates; `operonx add <unit> <feature> <name>` | each unit stamped into a scratch copy of hello, its test green |
| V2 | card format v2 (lane, unit, req, after, files, done); reader accepts v1; `requirements.md` | boards of mr-finance, callbot, tipcalc rewritten; nothing lost (diff of ids) |
| V3 | project page = board + chat; running card highlighted with its progress line; the other pages removed | screenshots light/dark; a 2-min test run never shows a line older than 30 s unannounced |
| V4 | assistant: breakdown turn, `card_set`, stop hook on a `doing` card | a Sonnet turn breaks a real task into cards with lanes and units |
| V5 | engine cut per §7 | engine and studio suites green; line counts before and after |
| V6 | one real mr-finance task end to end | cards on the board, every card done or needs-you, owner reads the board instead of the transcript |
