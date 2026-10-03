"""Fakes for tests: deterministic, offline, counting.

:class:`HashEmbedder` is an operonx ``BaseEmbedder`` whose vectors are signed
feature hashes of the text's words, L2-normalised. Texts that share words have
a positive cosine, so retrieval tests mean something without a model. Every call
is counted, which is how the incremental gates are measured ("a one-paragraph
edit re-embeds only the changed chunks").

:class:`OverlapReranker` is an operonx ``BaseReranker`` scoring a text by the
share of the query's words it contains; :class:`ScriptedLLM` is an operonx
``BaseLLM`` that answers from a script (a list of replies, or a function of the
messages), the scripted model the operonx guide recommends for tests.

They are resources like any other, registered when this module is imported.
operonx's provider ops resolve ``embedding:``, ``reranking:`` and ``llm:`` keys,
so a test points one of those at a fake with ``ResourceHub.alias``::

    fake_embedding:hash:
      dim: 64
    fake_reranking:overlap: {}
    fake_llm:scripted:
      responses: ['{"answer": "…", "citations": []}']

    hub.alias("reranking:overlap", "fake_reranking:overlap")
    hub.alias("llm:answerer", "fake_llm:scripted")
"""

from __future__ import annotations

import hashlib
import math
import re
from typing import Any, AsyncGenerator, Callable, ClassVar, Dict, List, Optional, Union

from operonx.core.registry import REGISTRY
from operonx.core.utils.yaml_model import YamlModel
from operonx.providers.embeddings.base import BaseEmbedder
from operonx.providers.llms.base import BaseLLM
from operonx.providers.rerankers.base import BaseReranker

__all__ = [
    "HashEmbeddingConfig",
    "HashEmbedder",
    "OverlapRerankerConfig",
    "OverlapReranker",
    "ScriptedLLMConfig",
    "ScriptedLLM",
    "register_fakes",
]

_WORDS = re.compile(r"\w+", re.UNICODE)


class HashEmbeddingConfig(YamlModel):
    """``fake_embedding:<name>``: a :class:`HashEmbedder`.

    Attributes:
        dim: Vector size.
        model: Reported as the model name; part of the embedder fingerprint.
    """

    _category: ClassVar[str] = "fake_embedding"

    dim: int = 64
    model: str = "hash-v1"


class HashEmbedder(BaseEmbedder):
    """Feature-hashing embedder that counts its calls.

    Attributes:
        calls: How many times :meth:`run` was called.
        texts: Every text it embedded, in order.
    """

    def __init__(self, config: HashEmbeddingConfig):
        self.config = config
        self.calls = 0
        self.texts: List[str] = []

    def reset(self) -> None:
        self.calls = 0
        self.texts = []

    def vector(self, text: str) -> List[float]:
        vec = [0.0] * self.config.dim
        for word in _WORDS.findall(text.lower()):
            h = int.from_bytes(hashlib.blake2b(word.encode("utf-8"), digest_size=8).digest(), "big")
            vec[h % self.config.dim] += 1.0 if (h >> 63) & 1 else -1.0
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]

    async def run(self, texts: Union[str, List[str]], **kwargs: Any) -> Dict[str, Any]:
        batch = [texts] if isinstance(texts, str) else list(texts)
        self.calls += 1
        self.texts.extend(batch)
        return {"embeddings": [self.vector(t) for t in batch]}

    def get_output_dim(self) -> int:
        return self.config.dim


class OverlapRerankerConfig(YamlModel):
    """``fake_reranking:<name>``: an :class:`OverlapReranker`."""

    _category: ClassVar[str] = "fake_reranking"

    model: str = "overlap-v1"


class OverlapReranker(BaseReranker):
    """Scores each text by the share of the query's distinct words it contains
    (ties keep the input order), and counts its calls.

    Attributes:
        calls: How many times :meth:`run` was called.
        pairs: How many (query, text) pairs it scored.
    """

    def __init__(self, config: OverlapRerankerConfig):
        self.config = config
        self.calls = 0
        self.pairs = 0

    async def run(
        self, query: str, texts: List[str], top_k: int = 3, threshold: float = 0.0, **kwargs: Any
    ) -> List[Dict]:
        self.calls += 1
        self.pairs += len(texts)
        words = set(_WORDS.findall(query.lower()))
        scored = []
        for i, text in enumerate(texts):
            have = set(_WORDS.findall(text.lower()))
            scored.append({"index": i, "score": len(words & have) / (len(words) or 1)})
        scored.sort(key=lambda r: (-r["score"], r["index"]))
        scored = [r for r in scored if r["score"] >= threshold]
        return scored[:top_k] if top_k and top_k > 0 else scored


class ScriptedLLMConfig(YamlModel):
    """``fake_llm:<name>``: a :class:`ScriptedLLM`.

    Attributes:
        responses: Replies in order; the last one repeats.
        model: Reported as the model name.
    """

    _category: ClassVar[str] = "fake_llm"

    responses: List[str] = []
    model: str = "scripted"


class ScriptedLLM(BaseLLM):
    """An operonx LLM backend that replies from a script and records every call.

    Set :attr:`script` to a function of the messages to answer from what the
    prompt holds (the sources of an answer prompt, say); otherwise the
    configured ``responses`` are returned in order.

    Attributes:
        messages: The messages of every call, in order.
    """

    def __init__(self, config: ScriptedLLMConfig):
        super().__init__(config)
        self.script: Optional[Callable[[List[Dict[str, Any]]], str]] = None
        self.messages: List[List[Dict[str, Any]]] = []

    def _reply(self, messages: List[Dict[str, Any]]) -> str:
        self.messages.append(list(messages))
        if self.script is not None:
            return self.script(list(messages))
        if not self.config.responses:
            raise ValueError("ScriptedLLM has no script and no responses")
        return self.config.responses[min(len(self.messages), len(self.config.responses)) - 1]

    async def generate(self, messages: List[Dict[str, Any]], **kwargs: Any) -> Any:
        from openai.types.chat.chat_completion import ChatCompletion, Choice
        from openai.types.chat.chat_completion_message import ChatCompletionMessage
        from openai.types.completion_usage import CompletionUsage

        content = self._reply(messages)
        prompt = sum(len(str(m.get("content", "")).split()) for m in messages)
        return ChatCompletion(
            id=f"scripted-{len(self.messages)}",
            created=0,
            model=self.config.model,
            object="chat.completion",
            choices=[Choice(index=0, finish_reason="stop",
                            message=ChatCompletionMessage(role="assistant", content=content))],
            usage=CompletionUsage(prompt_tokens=prompt, completion_tokens=len(content.split()),
                                  total_tokens=prompt + len(content.split())),
        )  # fmt: skip

    async def stream(self, messages: List[Dict[str, Any]], **kwargs: Any) -> AsyncGenerator:
        from openai.types.chat.chat_completion_chunk import ChatCompletionChunk, Choice, ChoiceDelta

        content = self._reply(messages)
        yield ChatCompletionChunk(
            id=f"scripted-{len(self.messages)}", created=0, model=self.config.model,
            object="chat.completion.chunk",
            choices=[Choice(index=0, delta=ChoiceDelta(role="assistant", content=content),
                            finish_reason="stop")],
        )  # fmt: skip


def register_fakes() -> None:
    """Register ``fake_embedding:``, ``fake_reranking:`` and ``fake_llm:`` (idempotent)."""
    for config, factory in (
        (HashEmbeddingConfig, HashEmbedder),
        (OverlapRerankerConfig, OverlapReranker),
        (ScriptedLLMConfig, ScriptedLLM),
    ):
        if REGISTRY.get_class(config._category) is None:
            REGISTRY.register(config, factory)


register_fakes()
