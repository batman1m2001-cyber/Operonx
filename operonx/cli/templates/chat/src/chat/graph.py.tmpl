"""The chat feature's wiring: no logic here, only ops and edges."""

from operonx import END, START, graph
from operonx.providers.ops import LLMOp

from chat._prompts import SYSTEM
from chat.ops import clean, reply


@graph
def chat_flow(question):  # served: the JSON body's `question`; the reply is {answer, error}
    q = clean(question=question)
    llm = LLMOp.of(
        resource="assistant",  # `llm:assistant` in resources.yaml
        prompt={"system": SYSTEM, "user": "{question}"},
        question=q["question"],  # every other keyword fills the template
    )
    r = reply(content=llm["content"], error=llm["error"])
    START >> q >> llm >> r >> END
