"""brd_flow with a scripted coder: checked extraction, fix turns, the miss-hunter, the folder."""

from __future__ import annotations

import subprocess

from operonx_project.brd.checker import check
from operonx_project.brd.folder import init_brd
from operonx_project.brd.records import load_questions, load_requirements
from operonx_project.brd.register import register_source
from operonx_project.brd.run import extract_source
from operonx_project.flows.brd_flow import BrdRuntime
from tests.brd_files import SAMPLE_BLOCKS, make_docx
from tests.scripted_coder import ScriptedCoder


def rec(rid, typ, section, quote, **kw):
    return {"id": rid, "type": typ, "section": section, "quote": quote, "reading": kw.pop("reading", rid), **kw}


BR1 = rec("BRD-BR-01", "business", "§2", "Gửi lời nhắc trước buổi học 24 giờ")
FAKE = rec("BRD-BR-02", "business", "§2", "Phụ huynh được hoàn tiền trong 7 ngày")  # not in the source
BR2 = rec("BRD-BR-02", "business", "§2", "Phụ huynh có thể hủy nhắc bằng một tin trả lời")
RULE1 = rec("BRD-RULE-CALL-01", "rule", "§3", "Không gọi điện cho phụ huynh sau 21 giờ",
            status="needs_clarification",
            question={"type": "ambiguity", "topic": "Whose clock is 21 giờ?", "fact": "§3: sau 21 giờ"})  # fmt: skip
MISSED = rec("BRD-RULE-CALL-02", "rule", "§3", "Mỗi phụ huynh nhận tối đa hai cuộc gọi mỗi ngày")
INVENTED = rec("BRD-RULE-CALL-03", "rule", "§3", "Cuộc gọi phải được ghi âm")


def _setup(tmp_path, script):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    init_brd(repo)
    brd = repo / "docs" / "brd"
    doc = make_docx(tmp_path / "client.docx", SAMPLE_BLOCKS)
    src = register_source(brd, doc, private=True, repo=repo, title="Client BRD")
    coder = ScriptedCoder(script)
    rt = BrdRuntime(coder=coder, data_dir=tmp_path / "state", key=f"brd-{tmp_path.name}")
    return rt, brd, doc, src, coder


async def test_checked_extraction_with_fix_turns_and_the_hunter(tmp_path):
    script = {
        "planner": [
            {"output": {"requirements": [BR1, FAKE, RULE1], "descriptive": []}},
            {"output": {"requirements": [BR2], "descriptive": [{"section": "§1", "reason": "an introduction"}]}},
        ],
        "reviewer": [{"output": {"requirements": [MISSED, INVENTED, BR1]}}],
    }
    rt, brd, doc, src, coder = _setup(tmp_path, script)
    out = await extract_source(rt, brd, doc, src.id, "BRD", "Client BRD", name="client_brd")
    assert out["problems"] == []
    assert out["requirements"] == 4 and out["found_by_hunter"] == ["BRD-RULE-CALL-02"]
    assert [r["id"] for r in out["rejected"]] == ["BRD-RULE-CALL-03"]  # the hunter's invention
    fix = coder.of("planner")[1]
    assert "does not occur in the source" in fix["prompt"] and "hoàn tiền" in fix["prompt"]
    assert "[§1]" in fix["prompt"]  # the skipped section, sent back
    assert fix["session_in"] == coder.of("planner")[0]["session"]  # the same session
    assert coder.of("reviewer")[0]["session_in"] is None  # the hunter: a session of its own
    reqs = {r.id: r for r in load_requirements(brd)}
    assert set(reqs) == {"BRD-BR-01", "BRD-BR-02", "BRD-RULE-CALL-01", "BRD-RULE-CALL-02"}
    assert reqs["BRD-RULE-CALL-01"].location == "§3 Quy tắc" and reqs["BRD-RULE-CALL-01"].question == "Q-01"
    assert [q.topic for q in load_questions(brd)] == ["Whose clock is 21 giờ?"]
    text = (brd / "requirements" / "client_brd_requirements.md").read_text()
    assert "hoàn tiền" not in text and "ghi âm" not in text  # no unverified quote is written
    errors, _ = check(brd)
    assert errors == [], errors


async def test_a_quote_never_fixed_is_not_written(tmp_path):
    script = {"planner": [{"output": {"requirements": [BR1, FAKE, RULE1, MISSED],
                                      "descriptive": [{"section": "§1", "reason": "intro"}]}}]}  # fmt: skip
    rt, brd, doc, src, coder = _setup(tmp_path, script)
    rt.hunt = False
    out = await extract_source(rt, brd, doc, src.id, "BRD", "Client BRD", name="client_brd")
    assert len(coder.of("planner")) == 3  # two fix turns, both ignored
    assert [r["id"] for r in out["rejected"]] == ["BRD-BR-02"]
    assert any("does not occur" in p for p in out["problems"])
    assert "BRD-BR-02" not in {r.id for r in load_requirements(brd)}


async def test_a_bracketed_section_and_a_letter_code(tmp_path):
    letter = rec("BRD-F01", "rule", "[§3]", "Mỗi phụ huynh nhận tối đa hai cuộc gọi mỗi ngày")
    script = {"planner": [{"output": {"requirements": [BR1, BR2, letter],
                                      "descriptive": [{"section": "[§1]", "reason": "intro"}]}}]}  # fmt: skip
    rt, brd, doc, src, coder = _setup(tmp_path, script)
    rt.hunt = False
    out = await extract_source(rt, brd, doc, src.id, "BRD", "Client BRD", name="client_brd")
    assert out["rejected"] == [] and out["problems"] == []
    assert {r.id: r.location for r in load_requirements(brd)}["BRD-F01"] == "§3 Quy tắc"
