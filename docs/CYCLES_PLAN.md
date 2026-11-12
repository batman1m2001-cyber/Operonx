---
title: One board, cut into cycles
status: planned
phases:
  - {id: K1, title: Cut what links to no card, status: planned}
  - {id: K2, title: Cycles in the engine; plan status blocks go, status: planned}
  - {id: K3, title: The Board page is the cycles, status: planned}
  - {id: K4, title: The boards rebuilt from the plans, status: planned}
updated: 2026-11-12
---

# One board, cut into cycles

**Asked** (owner, 2026-11-12, looking at mr-finance's Board page). "I can't follow this. There are tasks on the
kanban, then a Plans list below it with something in progress that has nothing to do with them, then 32 plans
without a status. Nothing links to anything." And then: "Make it one thing. If something can't be fitted into
the common design and linked to it, drop it." Then: "Each plan, or each development round, should be its own
kanban board," worked like a Jira sprint.

**Why it happened.** The Board page reads two sources that never meet. One is the cards in `docs/brd/board.md`.
The other is the status blocks of `docs/*_PLAN.md`, with the docs that have no block counted as "without a
status". The side column adds three more: the checklist, recent commits, and the standard. LEAN_PROJECT_PLAN's
cut list already named "plan status blocks → milestone mirror" and "pages: Checklist", but the studio kept the
Plans section and the Checklist section.

---

## 1. The one rule

**A cycle is a plan, and its phases are its cards.** Everything on the Board is a card in a cycle or in the
Backlog. Anything that can't hang off a card leaves the Board.

LEAN_PROJECT_PLAN's rule still holds: **every phase removes more than it adds.** Each PR states lines before and
after, in the engine and in the studio.

## 2. The picture

```
[ FF  6/7 ]  [ Backlog 8 ]                               Completed (n) ▾
 To do          Doing                     Needs you       Done
                C14 Pages and the                         C9  Pin every route's contract
                    assistant                             C10 A route's body contract
                                                          …
```

- **Tabs.** One tab per open cycle, then the Backlog. Closed cycles are in the **Completed** menu, as in Jira:
  a closed sprint leaves the board and stays readable as a report.
- **A completed cycle** opens read-only and shows its cards, the date it opened and closed, its plan doc, and its
  commits.
- **The side column** shows the same cards, filtered. "Needs you" is cards in *needs you*. "Working on" is cards
  in *doing*. Nothing else is listed there.

## 3. The file

It is still one file, `docs/brd/board.md`. Each `##` heading is a cycle, and the cards below a heading are its
cards. Cards before any `##`, or under `## Backlog`, are the Backlog.

```
## FF · One flow per feature · open
plan: docs/FEATURE_FLOWS_PLAN.md · opened: 2026-11-03

### C14 · Pages and the assistant · doing
files: src/pages/routes.py

## LP · One lean plan · closed
plan: docs/LEAN_PROJECT_PLAN.md · opened: 2026-11-11 · closed: 2026-11-11
```

- A cycle has an id, a title, and one of two states: *open* or *closed*. Its fields are `plan`, `opened` and
  `closed`.
- Cards keep their four fields and four states, and their ids stay global (`C<n>`), so `after:` still works
  across cycles.
- **Closing** refuses while any card in the cycle is not done. Each open card must first be moved to the Backlog
  or to another open cycle. This rule exists because of C1–C8: they are leftovers from milestone boards that
  closed with open cards, and nobody looked at them again.
- Closed cycles stay in the file, kept short: their cards keep only the heading line. `operonx-project status`
  skips them.

## 4. What goes

| On the page today | Links to a card? | What happens |
|---|---|---|
| The Plans section | yes: a plan becomes a cycle | merged into the Board |
| "32 plans without a status" | no | removed |
| A plan status block (`status:`, `phases:`) | the cycle holds it | removed: `plans.py`, `operonx-project plans`, the standard's "every plan says where it stands", and the stop hook's plan-phase checks |
| The Checklist section (x/y verified) | only through a card's `req` | the requirement ids show on their cards; the total leaves the Board |
| Recent changes | no | leaves the side column (the Git page has it) |
| Standard | no: it is setup, not work | moves to Settings only |
| Open BRD questions in "Needs you" | only if a card asks them | shown only through a card in *needs you* |

A plan doc keeps the why and the how. It no longer says where it stands.

## 5. The phases

**K1: Cut what links to no card (studio only).**
- Remove the Plans section, "plans without a status", the Checklist section and the Recent changes section.
- The Standard moves to Settings.
- Nothing is added.
- Gate: mr-finance's Board shows the kanban and nothing below it. Screenshots in light and dark.

**K2: Cycles in the engine; plan status blocks go.**
- `board.py` reads and writes `##` cycles.
- New commands: `cycle open <id> <title> [--plan]`, and `cycle close <id>`, which refuses while a card is open.
  `put --cycle` adds a card to a cycle.
- `status` gives the cycles. "Working on" becomes doing cards only.
- Remove `plans.py`, the `plans` command and the standard's plan check.
- Before each cut, grep for a second job. Known so far: the studio stop hook's `plans_done` and
  `plans_unreadable`, mcp's `unmarked_plans` and its `plans` target, and the studio's plan `Continue it`.
- Gate: tests pass. On tipcalc, a Sonnet session opens a cycle, works two cards, and closes the cycle. The close
  is refused while a card is open, and goes through once that card is moved to the Backlog.

**K3: The Board page is the cycles.**
- Add the tabs, the Backlog, and the Completed menu with its read-only report.
- The side column becomes card filters. The stop hook and mcp read cards, not plans.
- Gate: screenshots in light and dark, on mr-finance and on a second project.

**K4: The boards rebuilt from the plans.**
- A script turns every plan doc with a status block into a cycle: *done* becomes closed, anything else becomes
  open. Its phases become cards, and the status block is then stripped from the doc.
- In mr-finance, FEATURE_FLOWS becomes the open cycle FF (6 of 7 done) and C1–C8 go to the Backlog.
- In Operon and operonx-studio, their 5 plans each become cycles in a new `board.md`.
- The other plan docs (32 in mr-finance) get no board. They stay as docs.
- mr-finance changes only by PR. No operonx CLI runs inside `~/mr-finance`.
- Gate: the owner opens the Board and sees `[ FF 6/7 ] [ Backlog 8 ]  Completed ▾`, with nothing else on the page.

## 6. Out of scope

Sprint dates, velocity, burndown, estimates, assignees. A cycle is the plan's round of work, not a timebox.
