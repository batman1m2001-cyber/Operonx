"""Fakes for tests: deterministic, offline, counting.

:class:`HashEmbedder` is an operonx ``BaseEmbedder`` whose vectors are signed
feature hashes of the text's words, L2-normalised. Texts that share words have
a positive cosine, so retrieval tests mean something without a model. Every call
is counted, which is how the incremental gates are measured ("a one-paragraph
edit re-embeds only the changed chunks").

It is a resource like any other: ``fake_embedding:<name>`` in
``resources.yaml``, registered when this module is imported::

    fake_embedding:hash:
      dim: 64
"""

from __future__ import annotations

import hashlib
import math
import re
from typing import Any, ClassVar, Dict, List, Union

from operonx.core.registry import REGISTRY
from operonx.core.utils.yaml_model import YamlModel
from operonx.providers.embeddings.base import BaseEmbedder

__all__ = ["HashEmbeddingConfig", "HashEmbedder", "register_fakes"]

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


def register_fakes() -> None:
    """Register ``fake_embedding:`` (idempotent)."""
    if REGISTRY.get_class("fake_embedding") is None:
        REGISTRY.register(HashEmbeddingConfig, HashEmbedder)


register_fakes()
