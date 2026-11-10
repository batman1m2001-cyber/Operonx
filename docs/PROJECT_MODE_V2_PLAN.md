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

# Project mode v2 — one board, cards from units

**Asked** (owner, 2026-11-10). A Jira-like board. A task breaks into cards built from ready units; what no unit
covers is *flexible*. Layers kept apart. Templates only for patterns that really repeat. The running card is
highlighted and shows its progress. Nothing decorative.

---

## 1. Why

```mermaid
xychart-beta
  title "tipcalc builder run: 35 send-backs, by cause"
  x-axis ["held-out tests (QA)", "questions to a human", "review findings", "project checks"]
  y-axis "send-backs" 0 --> 20
  bar [16, 10, 5, 4]
```

**0 of 90** assistant turns were tied to a board card · engine **14.5k** lines · studio project mode **~2k** lines of JS.

```mermaid
flowchart TB
  subgraph today["today: one task"]
    direction LR
    a1[discover]:::cut --> a2[plan]:::cut --> a3[implement] --> a4[review]:::cut --> a5[held-out tests]:::cut --> a6[QA]:::cut --> a7[final]:::cut
  end
  subgraph v2["v2: one card"]
    direction LR
    b1[build from a unit] --> b2[its test] --> b3[project tests] --> b4[done]
  end
  today ~~~ v2
  classDef cut fill:#ffebe9,stroke:#d1242f,color:#82071e,stroke-dasharray:4 3
```

---

## 2. Architecture

![Architecture: studio page, assistant turn, repo, studio server, operonx](project-mode-v2/architecture.svg)

---

## 3. Lanes: clean architecture rings

```mermaid
flowchart LR
  UI["UI/UX<br/><small>web/pages</small>"]:::ui --> WEB["web backend<br/><small>Service · Job · bodies.py</small>"]:::web
  WEB --> FLOW["core · flow<br/><small>graph.py · ops.py</small>"]:::core
  FLOW --> DOM["core · domain<br/><small>rules · model · no I/O</small>"]:::core
  FLOW -- "via resources" --> DATA["data<br/><small>store · source</small>"]:::data
  FLOW -- "via resources" --> AI["AI<br/><small>llm · agent</small>"]:::ai
  PLAT["platform<br/><small>operonx.toml · resources.yaml · deploy</small>"]:::plat -.- WEB
  classDef ui fill:#8250df,stroke:#8250df,color:#fff
  classDef web fill:#0969da,stroke:#0969da,color:#fff
  classDef core fill:#1a7f37,stroke:#1a7f37,color:#fff
  classDef data fill:#9a6700,stroke:#9a6700,color:#fff
  classDef ai fill:#bf3989,stroke:#bf3989,color:#fff
  classDef plat fill:#6e7781,stroke:#6e7781,color:#fff
```

Arrow = *may import*. Domain imports nothing. Tests are not a lane: every card brings its own.

---

## 4. Units: the ready parts

```mermaid
mindmap
  root((10 units))
    UI/UX
      page
    web backend
      route
      job
    core
      flow
      op
      rule
    data
      store
      source
    AI
      llm
      agent
```

| unit | stamps | uses today (6 projects) | template from |
|---|---|---|---|
| op | `@op` in `ops.py` + unit test | 336 | `templates/hello` |
| flow | `graph.py` + run test | 91 | `templates/hello` |
| rule | pure module + test | 17 | tipcalc `money.py` |
| route | `Service(...)` + `bodies.py` + HTTP test | 93 | `templates/http`, mr-finance `news/bodies.py` |
| job | `Job(...)` + run test | 40 | mr-finance `app/main.py` |
| store | `store.py` (SQLite) + test | 15 | mr-finance `news/store.py` |
| source | fetch → normalise → store + fixture test | 5 | mr-finance `market/sources.py` |
| llm | `LLMOp` + prompt + fake-model test | 68 | `templates/chat` |
| agent | `AgentOp` + tools + fake-model test | 30 | `templates/agent` |
| page | `web/pages/<x>.js` + UI test | 39 | a small mr-finance page |

**Admission:** ≥ 3 uses in ≥ 2 projects, or ≥ 5 in one. Seen once or twice (`judge.py`, `mcp.py`, `golden.py`, `wav.py`) → stays flexible.

```mermaid
flowchart LR
  CMD["operonx add rule news threshold"] --> T[("_units/rule/")]
  T --> F1["src/news/threshold.py<br/><small>a stub with the signature</small>"]
  T --> F2["tests/news/test_threshold.py<br/><small>one red test</small>"]
  F1 & F2 --> C["card C4: files + done filled in"]
```

```mermaid
xychart-beta
  title "Code files that already have a unit's shape (%)"
  x-axis [tipcalc, qc, mp-brd, mp-ox, mrf, callbot]
  y-axis "%" 0 --> 100
  bar [85, 59, 60, 57, 47, 32]
```

qc = qc-snatcher · mp = meeting-prep (brd, operonx) · mrf = mr-finance. 49 % of 477 files across the six projects. The rest (audio, valuation, signing, retention) is real project code
and stays flexible.

---

## 5. Cards

![Three kinds of card: unit, flexible, fix](project-mode-v2/cards.svg)

What the assistant writes in `docs/brd/board.md`:

```markdown
### C4 · threshold rule · doing
lane: core · unit: rule · req: NEWS-07 · after: C2
files: src/news/threshold.py, tests/news/test_threshold.py
done: tests/news/test_threshold.py::test_gold_jump, project tests
```

![One card through its states](project-mode-v2/states.svg)

```mermaid
stateDiagram-v2
  direction LR
  state "needs you" as ask
  [*] --> waiting: after not done
  [*] --> todo
  waiting --> todo: after done
  todo --> doing: assistant starts it
  doing --> ask: a question, or a flexible card ready
  ask --> doing: owner answers
  ask --> done: owner approves
  doing --> done: tests green, not flexible
  done --> [*]
```

---

## 6. The board

![The project page: board with lanes × status, chat on the right](project-mode-v2/board.svg)

---

## 7. The implement flow

### 7a. A task becomes cards

```mermaid
flowchart TD
  T(["owner: alert me when gold moves more than 2% in a day"]) --> R["find its requirement,<br/>or add one to requirements.md"]
  R --> S["split the task by lane"]
  S --> B{"something broken?"}
  B -- "yes" --> F["fix card<br/><small>red test first</small>"]
  B -- "no" --> U{"a unit fits?"}
  U -- "yes" --> UC["unit card"]
  U -- "no" --> X["flexible card<br/><small>3-line design</small>"]
  F & UC & X --> O["order them with after:"]
  O --> G["board: all cards todo or waiting"]
  G --> GO{"owner"}
  GO -- "edit in chat" --> G
  GO -- "go" --> RUN(["work the cards in order"])
```

### 7b. The gold-alert task, as cards

```mermaid
flowchart LR
  C1["C1 prices store<br/><small>data · store</small>"]:::data --> C2["C2 gold feed<br/><small>data · source</small>"]:::data
  C2 --> C3["C3 daily move<br/><small>core · flexible</small>"]:::flex
  C2 --> C4["C4 threshold rule<br/><small>core · rule</small>"]:::core
  C3 & C4 --> C5["C5 /api/alerts/gold<br/><small>web · route</small>"]:::web
  C5 --> C6["C6 alert tile<br/><small>UI · page</small>"]:::ui
  classDef ui fill:#8250df,stroke:#8250df,color:#fff
  classDef web fill:#0969da,stroke:#0969da,color:#fff
  classDef core fill:#1a7f37,stroke:#1a7f37,color:#fff
  classDef data fill:#9a6700,stroke:#9a6700,color:#fff
  classDef flex fill:#fff8c5,stroke:#9a6700,color:#3b2300,stroke-dasharray:4 3
```

### 7c. Working the cards

```mermaid
sequenceDiagram
  autonumber
  actor O as Owner
  participant S as Studio board
  participant A as Assistant
  participant X as operonx add
  participant R as Repo
  O->>A: alert me when gold moves more than 2 percent a day
  A->>R: card_add C1 to C6, all todo
  R-->>S: 6 cards appear
  O->>A: go
  loop each card, in after order
    A->>R: card_set Cn doing
    R-->>S: Cn highlighted
    alt unit card
      A->>X: operonx add rule news threshold
      X->>R: stub and red test
      A->>R: fill the body
    else flexible card
      A->>R: design on the card, then code and test
    else fix card
      A->>R: red test first, then the fix
    end
    A->>R: the card's test, then the project tests
    R-->>S: live progress line on Cn
    alt green, and not flexible
      A->>R: card_set Cn done
    else flexible, or a question
      A->>R: card_set Cn needs-you
      O->>A: answer or approve
      A->>R: card_set Cn done
    end
  end
```

### 7d. Inside one card

```mermaid
flowchart TB
  subgraph unit["unit card"]
    direction LR
    u1["operonx add"] --> u2["fill the body"] --> u3["its test green"] --> u4["project tests green"] --> u5(["done"])
  end
  subgraph flex["flexible card"]
    direction LR
    x1["3-line design on the card"] --> x2["code + test"] --> x3["project tests green"] --> x4(["needs you: review the diff"])
  end
  subgraph fix["fix card"]
    direction LR
    f1["the red test"] --> f2["the fix"] --> f3["red test green"] --> f4["project tests green"] --> f5(["done"])
  end
  unit ~~~ flex ~~~ fix
```

No QA stage, no held-out tests: the card's test and the project's tests decide.

### 7e. The progress line

```mermaid
flowchart TD
  SH["a shell call<br/><small>pytest -q</small>"] -- "output + markers" --> OUT[("runhook out file")]
  OUT --> P["progress.read"]
  P -- "the tool printed a total" --> BAR["bar + n / total"]
  P -- "otherwise" --> LN["count + last line"]
  BAR & LN --> CARD["the running card"]
  CARD --> AGE{"last line older than 30 s?"}
  AGE -- "yes" --> SHOW["show its age"]
```

---

## 8. Keep and cut

| keep | cut |
|---|---|
| board store + reader (card format v2) | discover / plan / final flows |
| `brd check`, slimmed to `requirements.md` | review → held-out → QA stages, the examiner |
| `status`, one read-only summary | `practices.toml`, retro, LESSONS |
| studio turn checks, stop hook, `failing_on_base` | plan status blocks → milestone mirror |
| live progress (`progress.py`, runhook) | pages: Checklist · Questions · Changes · Graph |
| `operonx init` templates, plus `_units/` | decorative motion on the project page (`hand.js`) |

Before each cut: grep for a second job. The Telegram bridge (0.7k lines) stays until the owner says whether they use it.

---

## 9. Phases

```mermaid
flowchart TB
  subgraph r1[" "]
    direction LR
    V1["<b>V1</b> units<br/><small>_units/ + operonx add</small>"] --> V2["<b>V2</b> card format<br/><small>lane · unit · req · after · files · done</small>"]
    V2 --> V3["<b>V3</b> page<br/><small>board + chat, running card</small>"]
  end
  subgraph r2[" "]
    direction LR
    V4["<b>V4</b> assistant<br/><small>breakdown, card_set, stop hook</small>"] --> V5["<b>V5</b> engine cut<br/><small>per §8</small>"]
    V5 --> V6["<b>V6</b> gate<br/><small>one real mr-finance task</small>"]
  end
  r1 --> r2
  style r1 fill:none,stroke:none
  style r2 fill:none,stroke:none
```

| | gate |
|---|---|
| V1 | every unit stamped into a scratch copy of `hello`; its test green |
| V2 | mr-finance, callbot, tipcalc boards rewritten; no card id lost |
| V3 | screenshots light and dark; a 2-minute test run never shows a stale line without its age |
| V4 | a Sonnet turn breaks a real task into cards with lanes and units |
| V5 | engine and studio suites green; line counts before and after |
| V6 | the owner follows one real task on the board alone, without reading the transcript |
