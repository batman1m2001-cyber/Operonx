# D5 answers: a human check of the 30 in `d5_answers.jsonl`

Reviewed 2026-10-06 against each case's gold passage and each citation's source text. The
answers were made in the D5 run (`docs/bench/d5.md`); each was graded on what it says, and
each citation on whether the quoted text supports the sentence it marks.

## Verdict

| grade | n | cases |
|---|---|---|
| correct | 20 | 1–4, 6–13, 17, 19, 20, 22, 23, 27, 28, 30 |
| partial | 1 | 26 (a true statement from the right page, not the asked "reference prices") |
| wrong | 7 | 5, 14, 15, 16, 21, 25, 29 |
| abstained | 2 | 18, 24 (the page was not retrieved; the model said so) |

**No fabrication.** Every wrong answer is faithful to the passage it cites: the failures are
which passage was used, never an invented fact or a citation that does not say what the
sentence says. Abstentions are honest.

## Where the misses come from

- **Right document, wrong passage (4: 14, 15, 16, 29).** The gold sentence is in the same
  page as the cited one, further down. A larger `k` or sentence-window expansion around the
  best chunk (track5 §10.1) is the lever; not measured here.
- **Questions that need their paragraph (3: 5, 25, and 16 in part).** MLQA questions written
  against one paragraph: "Ông mất khi nào?" (who?), "the lowest temperature most of the
  year" (where?). Against a 692-document corpus no system can resolve them. **Filter such
  cases out of the eval set** (an unresolved pronoun or no named subject) before reading
  its numbers as retrieval quality.
- **Retrieval misses (2: 18, 24).** A legal abbreviation list outranks the Novell page for
  "SAP"; three other war pages outrank Ogaden for "a war shorter than a year".
- **A tautology (21):** "camping combined with hiking is called camping…". Its gold
  ("du hành") is itself a loose reading of the passage.

## A citation finding

Case 11 is correct but uncited: the model quoted the source **in English** ("the Lee
Strasberg Theatre Institute") while the page is Vietnamese. `ANSWER_PROMPT` already says
"never translated" (it did when these answers were made), so this is the model not
following it once in 30; verification did the right thing — dropped the quote and flagged
the sentence as unsupported, instead of showing a citation the page does not contain.
