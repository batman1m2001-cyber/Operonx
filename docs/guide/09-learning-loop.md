# The learning loop

An agent that solves the same problem the same way every week is doing
work it already did. The learning loop is the pattern where the agent
writes down what it figured out, in a form it will find again later.

Operonx ships the two halves — `Skill` files that get matched and injected
per turn, and `MemoryProvider` for durable facts. This page is about
wiring them into a loop, and about the failure modes that make a naive
version worse than no learning at all.

!!! warning "This is a pattern, not a feature"
    Nothing here is a framework primitive, and that is deliberate. What is
    worth remembering, when, and for how long are product decisions. A
    framework that answered them would be answering them for a use case it
    cannot see. Everything below is roughly forty lines of your code.

## The shape

```
      ┌──────────────── skills matched and injected ────────────────┐
      ▼                                                             │
   a turn runs ──▶ it worked ──▶ was this worth keeping? ──▶ write SKILL.md
                        │                    │
                   it failed            no ──┘ (most of the time)
```

Three questions, in the order they matter:

1. **Did the episode succeed?** Learning from a failure teaches the
   failure.
2. **Was it worth keeping?** Most episodes are not. This is the filter
   that decides whether the loop helps or poisons.
3. **Does it already know this?** Re-learning the same lesson in slightly
   different words is how a skills directory becomes noise.

## Reading: what the agent already knows

`load_skills` reads `SKILL.md` files; `build_react_agent` matches them per
turn against the user's message and injects the matches.

```python
from operonx.agents import build_react_agent, load_skills

skills = load_skills("./skills")
agent = build_react_agent(call_model=model, skills=skills)(messages=None)
```

A `SKILL.md` is frontmatter plus prose:

```markdown
---
name: deploy-staging
description: How to deploy this service to staging.
triggers: [deploy, staging, release]
---

Run `make deploy-staging`. It needs `AWS_PROFILE=staging` set.
The health check takes ~90s; do not retry before then.
```

`triggers` is what `match_skills` matches on. Without them it falls back
to the description, which works less well — a skill that never matches is
indistinguishable from one that was never written.

## Writing: what it just learned

The write side is yours. The minimum honest version:

```python
from pathlib import Path

WRITE_PROMPT = """\
Look at what just happened. If — and only if — you learned something \
reusable that you did not already know, write a SKILL.md for it.

Reusable means: it will apply to a *different* task later. A fact about \
this one file is not reusable. A convention this project follows is.

If there is nothing worth keeping, reply exactly: NOTHING.

Format:
---
name: <kebab-case>
description: <one line>
triggers: [<words that should surface this>]
---
<the lesson, in a few lines>
"""


async def maybe_learn(session, skills_dir: Path) -> str | None:
    result = await session.send(WRITE_PROMPT)
    body = (result["final"] or {}).get("content", "").strip()
    if not body or body.startswith("NOTHING"):
        return None

    name = _name_from_frontmatter(body)          # parse, don't trust
    path = skills_dir / name / "SKILL.md"
    if path.exists():
        return None                              # already known
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return name
```

Call it after a turn you have reason to believe succeeded — a passing test
suite, a merged change, an explicit thumbs-up. **Not after every turn.**

## The four ways this goes wrong

These are the whole reason the page exists.

### Learning from failure

An agent that writes a skill after every episode writes down its mistakes
with the same confidence as its successes, and then *retrieves* them. The
gate has to be an outcome signal you actually trust — tests passing, a
human accepting — not the model's own opinion that it did well.

### Drift

Skills are written from the codebase as it was. `make deploy-staging`
becomes `make deploy` and the skill now confidently teaches a command that
does not exist. Nothing detects this: retrieval has no idea the world
moved.

Two mitigations, both cheap:

- **Date them.** Put the date in the frontmatter and treat an old skill as
  a suspect one.
- **Delete on contradiction.** When a skill's advice fails, remove it.
  A skill that has been wrong once is more likely to be wrong again, and
  the loop has no other way to forget.

### Volume

Fifty skills is a retrieval problem, not a knowledge base. `match_skills`
takes a `limit` (3 by default) precisely because the cost of a skill is
paid on every turn that matches it.

Cap the directory. When it is full, the loop should be *replacing* the
weakest skill, not appending.

### Injection

If anything the agent reads can influence what it writes to `SKILL.md`,
then a file in the repo, a fetched page or a tool result can plant an
instruction the agent will follow on every future turn — with the
authority of its own notes.

This is the one that justifies caution rather than mitigation. If the
agent processes untrusted input, **a human reviews the diff before a
skill lands.** Writing to a branch and opening a PR is a perfectly good
version of the loop.

## Facts, not procedures

For "the staging DB is read-only", use memory rather than a skill:

```python
from operonx.agents import LocalMarkdownMemory

memory = LocalMarkdownMemory("./memory.md")
await memory.write("The staging DB is read-only.", source="agent")

agent = build_react_agent(call_model=model, memory_providers=[memory])(messages=None)
```

The distinction that matters in practice: **a skill is a procedure the
agent should follow when a situation arises; a memory is a fact it should
know.** `LocalMarkdownMemory` deduplicates on write, so re-learning the
same fact does not grow the file.

## Running it unattended

A learning loop pairs naturally with
[`Heartbeat`](../api/core.md) — a scheduled agent that works, then
reflects:

```python
from operonx.agents import Heartbeat

async def work_then_learn(_result):
    await maybe_learn(session, Path("./skills"))

hb = Heartbeat(session, "Check the queue and handle anything new.",
               interval=300, on_result=work_then_learn)
await hb.start()
```

Unattended is exactly where the four failure modes above bite hardest, and
where nobody is watching. If you run this way, keep the human review step
for skill writes — the schedule is what makes a bad skill compound.
