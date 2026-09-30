"""OpenAI-protocol embeddings: OpenAI itself, and Azure OpenAI.

The same client, and the same meaning of ``base_url``, as the ``llm:*``
backends in ``llms/openai.py`` and ``llms/azure.py``: ``base_url`` is the
**API root** (``https://api.openai.com/v1``) and the SDK adds
``/embeddings``. Azure differs only in how the client is built, so it
overrides :meth:`OpenAIEmbedding._client` and :meth:`OpenAIEmbedding._root`
and nothing else.

``api_type: vllm`` keeps :class:`~operonx.providers.embeddings.vllm.VLLMEmbedding`:
a server addressed by its exact endpoint URL.

Before 1.11.2 ``openai`` and ``azure`` were routed to that client too, so
the documented ``base_url: https://api.openai.com/v1`` posted to ``/v1``
and got a 404, ``dimensions`` was never sent, and Azure authenticated with
a bearer token and no ``api-version``.
"""

from __future__ import annotations

import warnings
from typing import Any, Dict, List, Optional, Union

from openai import AsyncAzureOpenAI, AsyncOpenAI

from operonx.providers._utils.http import create_http_client
from operonx.providers.embeddings.base import BaseEmbedder
from operonx.providers.embeddings.config import EmbeddingConfig

_ENDPOINT = "/embeddings"


class OpenAIEmbedding(BaseEmbedder):
    """``api_type: openai`` — any server that speaks OpenAI's embeddings API.

    ``dimensions``, when set, is both sent (models that can shorten their
    vectors do) and checked on every reply: a vector of another width
    raises here instead of reaching an index built for this one.
    """

    __slots__ = ["config", "client"]

    def __init__(self, config: EmbeddingConfig) -> None:
        if not config.base_url:
            raise ValueError(
                f"base_url is required for api_type: {config.api_type.value} — "
                "the API root, e.g. https://api.openai.com/v1"
            )
        if not config.model:
            raise ValueError(f"model is required for api_type: {config.api_type.value}")
        self.config = config
        self.client = self._client(self._root(config.base_url))

    def _root(self, base_url: str) -> str:
        """``base_url`` as an API root. A URL naming the endpoint itself
        (``…/v1/embeddings`` — the only form 1.11.1 accepted) still works,
        with a warning."""
        url = base_url.rstrip("/")
        if url.endswith(_ENDPOINT):
            root = url[: -len(_ENDPOINT)]
            warnings.warn(
                f"embedding base_url {base_url!r} names the endpoint; give the API root "
                f"{root!r} — the client adds {_ENDPOINT}",
                DeprecationWarning,
                stacklevel=4,
            )
            return root
        return url

    def _client(self, root: str) -> AsyncOpenAI:
        return AsyncOpenAI(
            base_url=root, api_key=self.config.api_key, http_client=create_http_client()
        )

    async def run(self, texts: Union[str, List[str]], **kwargs: Any) -> Dict[str, Any]:
        """Embed ``texts``; returns ``{"embeddings": [[float, ...], ...]}`` in input order."""
        if isinstance(texts, str):
            texts = [texts]
        if not isinstance(texts, list) or not all(isinstance(t, str) for t in texts):
            raise ValueError("Input must be a string or list of strings")
        if self.config.dimensions:
            kwargs.setdefault("dimensions", self.config.dimensions)
        response = await self.client.embeddings.create(
            model=self.config.model, input=texts, **kwargs
        )
        vectors = [d.embedding for d in sorted(response.data, key=lambda d: d.index)]
        _check(vectors, len(texts), self.config.dimensions, self.config.model)
        return {"embeddings": vectors}

    def get_output_dim(self) -> int:
        """The configured ``dimensions`` — the width every reply is checked against."""
        if not self.config.dimensions:
            raise ValueError(
                f"{self.config.model}: dimensions is not set in the resource, so the width is unknown"
            )
        return self.config.dimensions


class AzureOpenAIEmbedding(OpenAIEmbedding):
    """``api_type: azure`` — ``base_url`` is the resource endpoint
    (``https://<name>.openai.azure.com``), ``model`` the **deployment**
    name, and ``api_version`` is required."""

    __slots__ = []

    def __init__(self, config: EmbeddingConfig) -> None:
        if not config.api_version:
            raise ValueError("api_version is required for api_type: azure, e.g. 2024-10-21")
        super().__init__(config)

    def _root(self, base_url: str) -> str:
        url = base_url.rstrip("/")
        if "/openai/" in url + "/":
            raise ValueError(
                f"base_url {base_url!r} is a deployment URL; give the resource endpoint "
                "(https://<name>.openai.azure.com) and the deployment as model"
            )
        return url

    def _client(self, root: str) -> AsyncAzureOpenAI:
        return AsyncAzureOpenAI(
            azure_endpoint=root,
            api_key=self.config.api_key,
            api_version=self.config.api_version,
            http_client=create_http_client(),
        )


def _check(
    vectors: List[List[float]], expected: int, dimensions: Optional[int], model: Optional[str]
) -> None:
    """A reply that does not fit the request is an error, never a result."""
    if len(vectors) != expected:
        raise ValueError(f"{model}: asked for {expected} embeddings, got {len(vectors)}")
    if dimensions:
        widths = {len(v) for v in vectors}
        if widths != {dimensions}:
            raise ValueError(
                f"{model}: vectors of width {sorted(widths)}, but dimensions is {dimensions} — "
                "an index built for one cannot be searched with the other"
            )
