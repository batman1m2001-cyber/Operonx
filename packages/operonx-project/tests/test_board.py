"""The board (LEAN_PROJECT_PLAN §5): four fields, four states, the lane and the kind read, not written."""

from __future__ import annotations

import pytest

from operonx_project import board
from operonx_project.board import BoardError, Card

TEXT = """# Board

Text above the cards is kept.

### C1 · prices store · done
req: NEWS-01
files: src/news/store.py
done: tests/news/test_store.py::test_put

### C4 · gold jump alert · doing
req: NEWS-07 · after: C1
files: src/news/rules.py, tests/news/test_rules.py
done: tests/news/test_rules.py::test_gold_jump

### C5 · the dashboard tile · todo
after: C4
files: web/news.js

Which colour for a jump?
Red, or the lane's.
"""


def test_parse_and_render_round_trip():
    cards = board.parse(TEXT)
    assert [(c.id, c.state) for c in cards] == [("C1", "done"), ("C4", "doing"), ("C5", "todo")]
    c4 = cards[1]
    assert (c4.req, c4.after, c4.files) == (["NEWS-07"], ["C1"], ["src/news/rules.py", "tests/news/test_rules.py"])
    assert cards[2].note == "Which colour for a jump?\nRed, or the lane's."
    assert board.parse(board.render(cards, board._top(TEXT))) == cards
    assert board.render(cards, board._top(TEXT)).startswith("# Board\n\nText above the cards is kept.\n")


def test_hand_written_fields_are_read():
    # the demo's card: fields as list items, "none" for an empty one; they are fields, not the note
    c = board.parse("### C1 · sentences · todo\n- req: none · after: none\n- files: src/t/ops.py\n"
                    "- done: tests/test_t.py::test_sentences\n")[0]
    assert (c.req, c.after, c.files, c.note) == ([], [], ["src/t/ops.py"], "")
    assert c.done == ["tests/test_t.py::test_sentences"]
    assert c.kind == "fix"


def test_a_bad_heading_is_an_error():
    with pytest.raises(BoardError, match="line 1"):
        board.parse("### C1 · no state")


def test_lane_and_kind_are_read():
    assert board.lane(["web/news.js"]) == "UI"
    assert board.lane(["tests/news/test_rules.py", "src/news/routes.py"]) == "web"
    assert board.lane(["src/news/rules.py"]) == "core"
    assert board.lane(["src/news/store.py"]) == "data"
    assert board.lane(["src/news/prompts.py"]) == "AI"
    assert board.lane(["src/_shared/config.py"]) == board.lane(["app/main.py"]) == "platform"
    assert board.lane(["tests/x/test_y.py"]) == "core"
    assert Card("C1", "t", req=["A"]).kind == "build" and Card("C2", "t").kind == "fix"


def test_check_names_what_is_wrong():
    cards = board.parse(TEXT) + [Card("C4", "again", after=["C9"], req=["NOPE"], state="done")]
    errors = board.check(cards, defined={"NEWS-01", "NEWS-07"})
    assert "C4: two cards have this id" in errors
    assert "C4: after C9, which is no card" in errors
    assert "C4: req NOPE is not a requirement" in errors
    assert "C4: done, with no done test (a `done:` line under its heading)" in errors


def _repo(tmp_path):
    (tmp_path / "docs/brd").mkdir(parents=True)
    (tmp_path / board.PATH).write_text(TEXT, encoding="utf-8")
    return tmp_path


def test_put_adds_and_edits(tmp_path):
    c = board.put(tmp_path, title="first card", files=["src/a/ops.py"])
    assert (c.id, c.state) == ("C1", "todo") and (tmp_path / board.PATH).is_file()
    assert board.put(tmp_path, title="second").id == "C2"
    board.put(tmp_path, "C2", after=["C1"])
    assert board.read(tmp_path)[1].after == ["C1"]
    with pytest.raises(BoardError, match="after C7"):
        board.put(tmp_path, "C2", after=["C7"])
    with pytest.raises(BoardError, match="fields are"):
        board.put(tmp_path, "C2", lane=["UI"])


def test_move_follows_the_states(tmp_path):
    repo = _repo(tmp_path)
    with pytest.raises(BoardError, match="waits on C4"):
        board.move(repo, "C5", "doing")
    with pytest.raises(BoardError, match="no done test: give it one first"):
        board.move(repo, "C5", "done")
    assert board.move(repo, "C4", "done").state == "done"
    assert board.move(repo, "C5", "doing", note="").state == "doing"
    assert board.read(repo)[2].note == ""
    assert board.move(repo, "C5", "needs you", note="Red or amber?").note == "Red or amber?"
    with pytest.raises(BoardError, match="a state is one of"):
        board.move(repo, "C5", "waiting")


def test_a_new_file_name_needs_the_owner(tmp_path):
    board.put(tmp_path, title="a helper", files=["src/news/helpers.py", "src/news/_text.py"])
    c = board.move(tmp_path, "C1", "doing")
    assert c.state == "needs you" and "src/news/helpers.py" in c.note and "_text" not in c.note
    assert board.move(tmp_path, "C1", "doing", note="go").state == "doing"  # the owner said go


def test_view_marks_what_waits(tmp_path):
    v = board.view(board.read(_repo(tmp_path)))
    c5 = v["cards"][2]
    assert (c5["lane"], c5["kind"], c5["waits"]) == ("UI", "fix", ["C4"])
    assert v["columns"] == {"todo": ["C5"], "doing": ["C4"], "needs you": [], "done": ["C1"]}
