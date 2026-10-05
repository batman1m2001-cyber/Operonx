"""Concepts of a chunk, for the concept graph (PLAN G1, track5 §9.6 ``entities_lazy``).

No model: a concept is a **name** — a run of capitalised words, with the
lowercase connectors names carry inside (``Duke of Penthièvre``, ``Ludwig van
Beethoven``) — or a heading or title the chunk sits under. Two chunks that name
the same concept are linked through it, which is what a multi-hop question
needs: the paragraph about a film names its director, and the director's own
paragraph is titled with that name.

Names end at punctuation and at any lowercase word but a connector; leading
function words (``The``, ``In``, sentence starts) and trailing connectors are
dropped. Concepts are compared folded (casefold, no diacritics), so ``Hà Nội``
and ``Ha Noi`` are one concept. Vietnamese capitalises proper names the same
way, so the same rule holds there (``Hồ Chí Minh``, ``Nghệ An``).

Measured, not assumed (``docs/bench/k6.md``): content n-grams on top of names
added nothing on the dev split, so they are not extracted.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Dict, List, Sequence

from operonx_kb.model.ids import fingerprint
from operonx_kb.text.analyze import fold_diacritics
from operonx_kb.text.normalize import clean_chars

__all__ = ["VERSION", "names", "fold", "chunk_concepts", "concepts_fingerprint"]

#: Bump when extraction changes: concepts are committed with the version.
VERSION = "1"

_WORD = re.compile(r"[^\W_]+(?:['’\-][^\W_]+)*", re.UNICODE)
_FUNCTION = frozenset(
    """a an the of in on at to for from by with and or but is was were are be been being as that
    which who whom whose what when where why how this these those it its his her their there here
    than then so such not no nor did does do has have had he she they we you i me my your our us him
    them also into over after before about during between under while if
    ông bà anh chị các những một này đó theo trong tại khi sau vào từ là có và của cho với
    năm ngày tháng""".split()
)
#: Lowercase words a name may hold inside (``Duke of York``, ``Leonardo da Vinci``).
_CONNECTORS = frozenset("of the de du la le von van der den da di del y al bin ibn".split())


def fold(text: str) -> str:
    """A concept as compared: clean, casefolded, without diacritics, spaces collapsed."""
    return " ".join(fold_diacritics(clean_chars(text)).casefold().split())


def names(text: str) -> List[str]:
    """The names in ``text``, folded, in order, with repeats."""
    out: List[str] = []
    run: List[str] = []

    def flush() -> None:
        while run and run[-1].casefold() in _CONNECTORS:
            run.pop()
        while run and run[0].casefold() in _FUNCTION:
            run.pop(0)
        if run and (len(run) > 1 or len(run[0]) > 1):
            out.append(fold(" ".join(run)))
        run.clear()

    prev_end = None
    for m in _WORD.finditer(text):
        word = m.group()
        if prev_end is not None and text[prev_end : m.start()].strip():
            flush()  # punctuation between two words ends a name
        prev_end = m.end()
        if word[:1].isupper() or (run and word[:1].isdigit()):
            run.append(word)
        elif run and word in _CONNECTORS:
            run.append(word)
        else:
            flush()
    flush()
    return out


def chunk_concepts(text: str, headings: Sequence[str], title_weight: float) -> Dict[str, float]:
    """A chunk's concepts and their edge weights: 1 per mention of a name in the
    text, ``title_weight`` for each heading (the whole heading, and the names in it)."""
    weights: Counter = Counter(names(text))
    seen = set()
    for heading in headings:
        whole = fold(heading)
        if not whole or whole in seen:  # the title is often the first heading too
            continue
        seen.add(whole)
        for concept in {whole, *names(heading)}:
            weights[concept] += title_weight
    return dict(weights)


def concepts_fingerprint(title_weight: float) -> str:
    """What the committed concepts depend on (part of ``pipeline_fp``)."""
    return fingerprint("operonx_kb.enrich.concepts", VERSION, {"title_weight": title_weight})
