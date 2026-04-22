"""Shared workflow definitions for ex03_llm_chat.

Defines LLM chat graphs.

Cần: OPENAI_API_KEY hoặc OPENROUTER_API_KEY trong .env
"""

from operon.core import END, PARENT, START, GraphOp, op
from operon.providers import LLMOp, PromptOp, chat


@op
def clean_text(text: str):
    """Tiền xử lý văn bản: loại bỏ khoảng trắng thừa."""
    cleaned = " ".join(text.split()).strip()
    return {"cleaned_text": cleaned}


def build_basic_chat() -> GraphOp:
    """PromptOp.of() + LLMOp.of() — Cách cơ bản nhất."""
    with GraphOp(name="basic-chat") as graph:
        p = PromptOp.of(
            template={
                "system": "Bạn là trợ lý AI thân thiện. Trả lời ngắn gọn.",
                "user": "{question}",
            },
            question=PARENT["question"],
        )
        llm = LLMOp.of(
            resource="gpt-4o-mini",
            messages=p["messages"],
            outputs={"content": PARENT["answer"]},
        )
        START >> p >> llm >> END
    return graph


def build_chain_chat() -> GraphOp:
    """chain() — All-in-one, gọn hơn."""
    with GraphOp(name="chain-chat") as graph:
        c = chat(
            resource="gpt-4o-mini",
            template={
                "system": "Bạn là assistant hữu ích. Trả lời ngắn gọn.",
                "user": "{query}",
            },
            query=PARENT["query"],
            outputs={"content": PARENT["response"]},
        )
        START >> c >> END
    return graph


def build_summarize() -> GraphOp:
    """Pipeline: tiền xử lý → prompt → LLM."""
    with GraphOp(name="summarize-pipeline") as graph:
        preprocess = clean_text(text=PARENT["text"])
        p = PromptOp.of(
            template={
                "system": "Bạn là chuyên gia tóm tắt văn bản. Tóm tắt ngắn gọn trong 1-2 câu.",
                "user": "Tóm tắt:\n\n{text}",
            },
            text=preprocess["cleaned_text"],
        )
        summarize = LLMOp.of(
            resource="gpt-4o-mini",
            messages=p["messages"],
            outputs={"content": PARENT["summary"]},
        )
        START >> preprocess >> p >> summarize >> END
    return graph
