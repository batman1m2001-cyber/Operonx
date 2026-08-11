"""Skills — markdown files that teach an agent a procedure.

A skill is a `SKILL.md`: YAML frontmatter naming it and saying when it
applies, then a markdown body explaining how. The agent loads them at
startup and injects the relevant ones per turn.

Why files rather than a longer system prompt: a system prompt carrying
every procedure the agent might need pays for all of them on every turn,
and grows until nothing else fits. Skills are selected per query, so the
cost scales with what is actually relevant.

**Skills inject as a user message, never into the system prompt.** They
change with the query, and the system prompt is the cached prefix — so
putting them there is a cache miss on every turn plus a prompt that grows
without bound. Same reasoning as retrieved memory, and the same
placement.

Matching is keyword overlap against the skill's declared triggers, not an
embedding search. A skill that fails to match is invisible, so the
selection has to be inspectable by the person who wrote the triggers; a
similarity score they cannot reason about is worse than a rule they can.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from operonx.core.loggings import LOGGER
from operonx.core.ops.transform.func_op import op

__all__ = ["Skill", "load_skills", "match_skills", "render_skills", "inject_skills"]

_FRONTMATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?(.*)\Z", re.DOTALL)
_WORD = re.compile(r"[a-z0-9]+")


@dataclass
class Skill:
    """One loaded skill."""

    name: str
    description: str
    body: str
    triggers: List[str] = field(default_factory=list)
    always: bool = False
    path: str = ""

    @property
    def tokens(self) -> set[str]:
        """Trigger vocabulary. Falls back to the description when no
        triggers were declared, so a skill without them still has a
        chance of matching rather than silently never firing."""
        source = " ".join(self.triggers) if self.triggers else self.description
        return {w for w in _WORD.findall(source.lower()) if len(w) > 1}


def _parse(text: str, path: str) -> Optional[Skill]:
    """Parse one SKILL.md. Returns ``None`` if it is not usable."""
    match = _FRONTMATTER.match(text)
    if not match:
        LOGGER.warning("skills: %s has no YAML frontmatter; skipped", path)
        return None

    try:
        import yaml

        meta = yaml.safe_load(match.group(1)) or {}
    except Exception as exc:  # noqa: BLE001 - one bad file must not stop startup
        LOGGER.warning("skills: %s frontmatter did not parse (%s); skipped", path, exc)
        return None

    if not isinstance(meta, dict):
        LOGGER.warning("skills: %s frontmatter is not a mapping; skipped", path)
        return None

    name = str(meta.get("name") or Path(path).parent.name or Path(path).stem).strip()
    description = str(meta.get("description") or "").strip()
    if not name or not description:
        # Without a description the agent cannot tell what the skill is
        # for, and a skill that never matches is worse than absent —
        # absent is at least obvious.
        LOGGER.warning("skills: %s needs both `name` and `description`; skipped", path)
        return None

    raw_triggers = meta.get("triggers") or meta.get("when_to_use") or []
    if isinstance(raw_triggers, str):
        raw_triggers = [raw_triggers]
    triggers = [str(t) for t in raw_triggers if str(t).strip()]

    return Skill(
        name=name,
        description=description,
        body=match.group(2).strip(),
        triggers=triggers,
        always=bool(meta.get("always")),
        path=path,
    )


def load_skills(root: str | Path, pattern: str = "**/SKILL.md") -> List[Skill]:
    """Load every skill under ``root``.

    A malformed file is logged and skipped rather than raising. Skills are
    optional enrichment, and one bad file should not stop an agent
    starting — but the warning names the file, because a skill that
    silently never loads is indistinguishable from one that never
    matches.
    """
    base = Path(root)
    if not base.is_dir():
        LOGGER.warning("skills: %s is not a directory; no skills loaded", base)
        return []

    skills: List[Skill] = []
    seen: Dict[str, str] = {}
    for path in sorted(base.glob(pattern)):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            LOGGER.warning("skills: could not read %s (%s); skipped", path, exc)
            continue
        skill = _parse(text, str(path))
        if skill is None:
            continue
        if skill.name in seen:
            # Two skills answering to one name means one of them is
            # unreachable, and which one depends on directory order.
            LOGGER.warning(
                "skills: duplicate name %r in %s (already defined by %s); skipped",
                skill.name,
                path,
                seen[skill.name],
            )
            continue
        seen[skill.name] = str(path)
        skills.append(skill)
    return skills


def match_skills(query: str, skills: Iterable[Skill], limit: int = 3) -> List[Skill]:
    """Select the skills relevant to ``query``.

    ``always`` skills are returned first and do not count against
    ``limit`` — they were declared unconditional, and silently dropping
    one because three keyword matches outranked it would make the flag a
    lie.
    """
    skills = list(skills)
    always = [s for s in skills if s.always]

    wanted = {w for w in _WORD.findall((query or "").lower()) if len(w) > 1}
    scored: List[tuple[float, Skill]] = []
    for skill in skills:
        if skill.always:
            continue
        tokens = skill.tokens
        if not tokens:
            continue
        overlap = len(wanted & tokens)
        if overlap:
            scored.append((overlap / len(tokens), skill))

    scored.sort(key=lambda pair: pair[0], reverse=True)
    return always + [skill for _, skill in scored[: max(0, limit)]]


def render_skills(skills: Iterable[Skill]) -> Optional[str]:
    """Render selected skills as one block, or ``None`` if none matched.

    ``None`` rather than an empty block: an always-present wrapper that
    is sometimes empty still changes the prompt on the turns it is empty.
    """
    skills = list(skills)
    if not skills:
        return None
    parts = [f"<skill name={s.name!r}>\n{s.description}\n\n{s.body}\n</skill>" for s in skills]
    return "<skills>\n" + "\n\n".join(parts) + "\n</skills>"


@op
def inject_skills(
    query: str = "",
    skills: Optional[list] = None,
    limit: int = 3,
) -> dict:
    """Select and render skills for this turn.

    Returns ``message=None`` when nothing matched, so the caller can drop
    it rather than send an empty block. The message is **user**-role
    deliberately: skills change per query and the system prompt is the
    cached prefix.
    """
    selected = match_skills(query, [s for s in (skills or []) if isinstance(s, Skill)], limit)
    block = render_skills(selected)
    message = {"role": "user", "content": block} if block else None
    return {
        "message": message,
        # `notices` is the list shape `assemble_api_messages` consumes.
        # Empty rather than [None] when nothing matched — an empty block
        # still changes the prompt on the turns it is empty.
        "notices": [message] if message else [],
        "names": [s.name for s in selected],
        "count": len(selected),
    }


def skill_summaries(skills: Iterable[Skill]) -> str:
    """One line per skill, cheap enough for the system prompt.

    The full bodies are per-turn; this index is not. Telling the model
    which skills *exist* is what lets it ask for one whose triggers did
    not fire — otherwise a skill is only ever reachable by accident of
    wording.
    """
    lines = [f"- {s.name}: {s.description}" for s in sorted(skills, key=lambda s: s.name)]
    return "\n".join(lines)
