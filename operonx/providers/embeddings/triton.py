"""Embeddings served by Triton Inference Server (gRPC).

A deployment either holds the tokeniser or it does not, and the two take
different tensors. ``input_name`` selects which:

===================  =========================================  ============
``input_name``       sends                                      truncated by
===================  =========================================  ============
unset                ``input_ids`` / ``attention_mask`` INT64   this client
set, e.g. ``TEXT``   that one input, utf-8 strings as BYTES     the server
===================  =========================================  ============

Unset is the original contract: Triton serves weights, its inputs are
token ids, and ``tokenizer_path`` is required — it cannot be derived from
``model``, which for Triton is a *served model name*, not a directory.

Set it when the deployment has absorbed the tokeniser. No tokeniser is
loaded, ``tokenizer_path`` is not required, and **``max_length`` stops
meaning anything** because the server decides where to cut. That is worth
saying out loud: a server truncating at a different length than the one an
index was built with returns a different vector for any text long enough
to be cut, and nothing in the response says so.

The op contract is ``texts -> embeddings`` either way. Token ids never
appear in a signature: putting them there would leak the backend into the
graph and make ``triton`` non-substitutable for ``tei`` / ``vllm`` /
``onnx``, which take raw text. What differs between backends is *where*
inference runs, not what a caller passes.

``output_name`` matters in both modes. It is taken directly, **never
pooled**: BGE-M3 exports both ``token_embeddings`` and
``sentence_embedding``, and mean-pooling the former gives a different
vector from the latter. Pin it in the resource rather than relying on a
default.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple, Union

import numpy as np

from operonx.providers.embeddings.base import BaseEmbedder
from operonx.providers.embeddings.config import EmbeddingConfig

__all__ = ["TritonEmbedding"]

# Matches ONNXEmbedding's fallback. The two backends are meant to be
# swappable, so an unset `max_length` must not silently mean 256 on one
# and 512 on the other — that would be a config-shaped vector mismatch
# with no error anywhere. Pin it in the resource.
_DEFAULT_MAX_LENGTH = 512

# Texts per inference request. Triton applies its own dynamic batching
# across concurrent requests; this only bounds gRPC message size.
_DEFAULT_BATCH = 16


class TritonEmbedding(BaseEmbedder):
    """Embeddings from a Triton-served model over gRPC.

    Requires: ``pip install tritonclient[grpc] tokenizers``
    """

    __slots__ = [
        "config",
        "tokenizer",
        "_url",
        "_ssl",
        "_model",
        "_output_name",
        "_input_name",
        "_max_length",
        "_batch_size",
        "_output_dim",
    ]

    def __init__(self, config: EmbeddingConfig) -> None:
        """Record the endpoint, and load a tokeniser only if we need one.

        The gRPC channel is *not* opened here — ``TritonClient.get()``
        caches one per ``(url, ssl)`` process-wide, so opening it lazily
        keeps construction cheap and shares the channel with any other
        resource pointing at the same endpoint.

        With ``input_name`` set the server tokenises, so neither
        ``tokenizers`` nor ``tokenizer_path`` is needed and neither is
        touched — a deployment that has absorbed its tokeniser should not
        have to ship one to its clients as well.

        Raises:
            ImportError: ``tokenizers`` is missing and this mode needs it.
            ValueError: ``base_url`` or ``model`` is missing, or
                ``tokenizer_path`` is missing in token-id mode, or the
                tokeniser file cannot be read.
        """
        if not config.base_url:
            raise ValueError(
                "TritonEmbedding requires base_url= — the Triton gRPC endpoint "
                "as host:port (no scheme), e.g. 'triton.internal:443'."
            )
        if not config.model:
            raise ValueError(
                "TritonEmbedding requires model= — the name the model is served "
                "under in Triton, e.g. 'bge_m3'."
            )

        self.config = config
        self._url = config.base_url
        self._ssl = bool(config.ssl)
        self._model = config.model
        self._output_name = config.output_name or "sentence_embedding"
        self._input_name = config.input_name or None
        self._max_length = config.max_length or _DEFAULT_MAX_LENGTH
        self._batch_size = config.embed_batch_size or _DEFAULT_BATCH
        self._output_dim = None
        self.tokenizer = None

        if self._input_name:
            # Server-side tokenisation. `max_length` is the server's to
            # decide and is deliberately not applied here, so leaving it
            # set in a resource is inert rather than a half-applied cut.
            return

        try:
            from tokenizers import Tokenizer
        except ImportError as e:
            raise ImportError(
                "TritonEmbedding requires additional packages.\n"
                "  Install with: pip install tritonclient[grpc] tokenizers\n"
                "  Or set input_name= if the deployment tokenises server-side.\n"
                f"  Original error: {e}"
            ) from e

        if not config.tokenizer_path:
            raise ValueError(
                "TritonEmbedding requires tokenizer_path= unless input_name= is "
                "set. Without input_name the server's inputs are token ids, so "
                "the client must tokenise: point this at the tokenizer.json that "
                "matches the served model. If the deployment takes raw text "
                "instead, set input_name to that input, e.g. input_name='TEXT'."
            )

        try:
            self.tokenizer = Tokenizer.from_file(str(config.tokenizer_path))
        except Exception as e:
            raise ValueError(
                f"Failed to load tokenizer from '{config.tokenizer_path}': {e}"
            ) from e
        self.tokenizer.enable_truncation(max_length=self._max_length)

    # ── tensor construction ───────────────────────────────────────────

    def _encode_batch(self, texts: List[str]) -> Tuple[np.ndarray, np.ndarray]:
        """Tokenise into rectangular INT64 ``(input_ids, attention_mask)``.

        Padding is built by hand rather than via the tokeniser's own
        ``enable_padding``: the wire format is a rectangular tensor, and
        writing it here keeps the padding rule visible next to the mask
        that has to agree with it.

        Width is the longest sequence in *this* batch, capped at
        ``max_length``. Batch width does not change the result — pad
        columns carry ``attention_mask == 0``, so a masked model's
        outputs for real tokens are unaffected by how many pads follow.
        """
        enc = self.tokenizer.encode_batch(texts)
        # Floor of 1: a tokeniser that emits no special tokens turns a batch
        # of empty strings into a zero-width tensor, which Triton rejects.
        # Tokenisers that do add [CLS]/[SEP] never hit this.
        seq = max(min(max(len(e.ids) for e in enc), self._max_length), 1)
        ids = np.zeros((len(texts), seq), dtype=np.int64)
        mask = np.zeros((len(texts), seq), dtype=np.int64)
        for j, e in enumerate(enc):
            n = min(len(e.ids), seq)
            ids[j, :n] = e.ids[:n]
            mask[j, :n] = 1
        return ids, mask

    @staticmethod
    def _text_batch(texts: List[str]) -> np.ndarray:
        """Pack texts as the ``(n, 1)`` BYTES tensor Triton expects.

        Shape ``(n, 1)`` rather than ``(n,)`` because the served model
        declares one string per row; a flat vector is a rank mismatch the
        server rejects. Encoding to utf-8 here rather than leaving numpy
        to pick makes the wire bytes explicit — a ``<U`` array would let
        the client library choose, and Vietnamese is exactly where that
        choice shows up.
        """
        return np.array([[t.encode("utf-8")] for t in texts], dtype=object)

    @staticmethod
    def _normalize(embeddings: np.ndarray) -> np.ndarray:
        """L2-normalise row-wise, guarding a zero vector."""
        norms = np.linalg.norm(embeddings, ord=2, axis=1, keepdims=True)
        return embeddings / norms.clip(min=1e-12)

    # ── BaseEmbedder ──────────────────────────────────────────────────

    async def run(self, texts: Union[str, List[str]], **kwargs: Any) -> Dict[str, Any]:
        """Embed one or more texts.

        Args:
            texts: A string or list of strings.
            **kwargs: ``max_length`` overrides the configured truncation
                for this call — ignored when ``input_name`` is set, because
                the server owns truncation there; ``timeout`` (seconds)
                bounds each request.

        Returns:
            ``{"embeddings": [[float, ...], ...]}`` — L2-normalised, one
            row per input text, in input order.

        Raises:
            ValueError: If ``texts`` is not a string or list of strings.
            RuntimeError: If Triton returns no tensor under
                ``output_name``, or returns the wrong number of rows.
        """
        from operonx.providers.triton import TritonClient

        if isinstance(texts, str):
            texts = [texts]
        if not isinstance(texts, list) or not all(isinstance(t, str) for t in texts):
            raise ValueError("Input must be a string or list of strings")
        if not texts:
            return {"embeddings": []}

        max_length = kwargs.get("max_length")
        if max_length and max_length != self._max_length and self.tokenizer:
            self._max_length = max_length
            self.tokenizer.enable_truncation(max_length=max_length)
        timeout = kwargs.get("timeout", 30.0)

        client = TritonClient.get(self._url, ssl=self._ssl)

        chunks: List[np.ndarray] = []
        for i in range(0, len(texts), self._batch_size):
            batch = texts[i : i + self._batch_size]
            if self._input_name:
                inputs = {self._input_name: self._text_batch(batch)}
            else:
                ids, mask = self._encode_batch(batch)
                inputs = {"input_ids": ids, "attention_mask": mask}

            # decode=False: this output is numeric, and the text-decoding
            # path would only ever pass it through.
            result = await client.infer(
                model=self._model,
                inputs=inputs,
                outputs=[self._output_name],
                timeout=timeout,
                decode=False,
            )

            vec = result.get(self._output_name)
            # `infer` maps an unreadable output to None and logs a warning.
            # For an embedder that is silent corruption — the caller would
            # index or search with a null vector — so it has to raise.
            if vec is None:
                raise RuntimeError(
                    f"Triton model '{self._model}' returned no tensor named "
                    f"'{self._output_name}'. Check the model's output names; "
                    "set output_name= in the embedding resource to match."
                )
            vec = np.asarray(vec, dtype=np.float32)
            if vec.ndim != 2 or vec.shape[0] != len(batch):
                raise RuntimeError(
                    f"Triton model '{self._model}' returned shape {vec.shape} for "
                    f"{len(batch)} inputs; expected ({len(batch)}, dim). Output "
                    f"'{self._output_name}' is probably per-token rather than "
                    "pooled — point output_name= at the pooled output."
                )
            chunks.append(vec)

        embeddings = self._normalize(np.concatenate(chunks, axis=0))
        if self._output_dim is None:
            self._output_dim = int(embeddings.shape[1])
        return {"embeddings": embeddings.tolist()}

    def get_output_dim(self) -> int:
        """Embedding dimensionality.

        Prefers what the server actually returned, then the configured
        ``dimensions``. Unlike the local backends this cannot fall back
        to running a probe text — that would be a network call from a
        sync method.

        Raises:
            RuntimeError: If nothing has been embedded yet and
                ``dimensions`` is unset.
        """
        if self._output_dim is not None:
            return self._output_dim
        if self.config.dimensions:
            return self.config.dimensions
        raise RuntimeError(
            "Output dimension is unknown until the first embed call. Set "
            "dimensions= in the embedding resource to declare it up front."
        )
