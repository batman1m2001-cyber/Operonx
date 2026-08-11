"""Async Triton Inference Server client with a dict-in / dict-out ``infer``.

This is the low-level helper user ``@op``s call directly when no semantic
op fits their model::

    from operonx.core import op
    from operonx.providers.triton import TritonClient

    @op(bound="io")
    async def stt(speech_audio):
        client = TritonClient.get("localhost:8001")
        result = await client.infer(
            model="fastconformer_asr",
            inputs={"AUDIO_SIGNAL": speech_audio},
            outputs=["TRANSCRIPT", "EMBEDDING"],
        )
        return {"transcript": result["TRANSCRIPT"], "embedding": result["EMBEDDING"]}

``TritonClient.get()`` returns a **process-cached** client per URL so the
underlying gRPC channel is reused across calls. Building a fresh channel
per inference adds connection setup to every request — measurable on
real-time paths. Always go through ``get()`` rather than constructing
``TritonClient`` directly.
"""

import logging
from typing import Any, Dict, List, Optional

from operonx.providers.triton.decode import decode_infer_output
from operonx.providers.triton.dtypes import numpy_to_triton_dtype, to_infer_array

LOGGER = logging.getLogger(__name__)

__all__ = ["TritonClient", "get_aio_grpcclient"]


# Lazily imported ``tritonclient.grpc.aio`` module.
_aio_grpcclient = None

# Process-wide cache of TritonClient instances, keyed by URL. Holds the
# gRPC channel open across calls — see module docstring.
_clients: Dict[str, "TritonClient"] = {}


def get_aio_grpcclient():
    """Import and cache ``tritonclient.grpc.aio``.

    Raises:
        ImportError: With an install hint when tritonclient is absent.
    """
    global _aio_grpcclient
    if _aio_grpcclient is None:
        try:
            import tritonclient.grpc.aio as aio_grpc

            _aio_grpcclient = aio_grpc
        except ImportError as e:
            raise ImportError(
                "tritonclient is required for Triton inference.\n"
                "  Install with: pip install tritonclient[grpc]\n"
                f"  Original error: {e}"
            ) from e
    return _aio_grpcclient


class TritonClient:
    """Async Triton gRPC client with dict-in / dict-out inference.

    Use :meth:`get` rather than the constructor so the gRPC channel is
    shared process-wide per URL.

    Attributes:
        url: Triton gRPC endpoint (``host:port``).
    """

    __slots__ = ("url", "_raw")

    def __init__(self, url: str):
        """Construct a client. Prefer :meth:`get` — see class docstring."""
        aio_grpc = get_aio_grpcclient()
        self.url = url
        self._raw = aio_grpc.InferenceServerClient(url=url)

    @classmethod
    def get(cls, url: str) -> "TritonClient":
        """Return the process-cached client for ``url``, creating it once.

        Args:
            url: Triton gRPC endpoint (``host:port``).

        Returns:
            A shared :class:`TritonClient`. The same instance — and the
            same underlying gRPC channel — is returned for repeat calls
            with the same URL.
        """
        if url not in _clients:
            _clients[url] = cls(url)
        return _clients[url]

    @property
    def raw(self):
        """The underlying ``tritonclient.grpc.aio.InferenceServerClient``.

        Escape hatch for Triton features this wrapper doesn't surface
        (model metadata, health checks, streaming inference).
        """
        return self._raw

    async def infer(
        self,
        model: str,
        inputs: Dict[str, Any],
        outputs: List[str],
        *,
        model_version: str = "",
        timeout: float = 30.0,
        decode: bool = True,
    ) -> Dict[str, Any]:
        """Run one inference request.

        Input values are coerced to numpy and dtype-mapped automatically;
        ``None`` values are skipped so optional model inputs can be
        omitted by passing ``None``.

        Args:
            model: Triton model name.
            inputs: ``{triton_input_name: array-like}``. Values of
                ``None`` are skipped.
            outputs: Triton output tensor names to request.
            model_version: Model version; ``""`` means latest.
            timeout: Client-side timeout in seconds.
            decode: When True (default), text tensors are decoded to
                ``str`` via :func:`~operonx.providers.triton.decode.decode_infer_output`.
                Set False to receive raw numpy arrays.

        Returns:
            ``{triton_output_name: value}``. An output that fails to
            decode maps to ``None`` and logs a warning rather than
            failing the whole request.

        Raises:
            Exception: Propagates transport / inference errors from
                tritonclient after logging them.
        """
        aio_grpc = get_aio_grpcclient()

        infer_inputs = []
        for name, data in inputs.items():
            if data is None:
                continue
            arr = to_infer_array(data)
            inp = aio_grpc.InferInput(name, list(arr.shape), numpy_to_triton_dtype(arr))
            inp.set_data_from_numpy(arr)
            infer_inputs.append(inp)

        infer_outputs = [aio_grpc.InferRequestedOutput(name) for name in outputs]

        # Async inference — lets Triton apply dynamic batching across
        # concurrent requests.
        try:
            result = await self._raw.infer(
                model_name=model,
                model_version=model_version,
                inputs=infer_inputs,
                outputs=infer_outputs,
                client_timeout=timeout,
            )
        except Exception as e:
            LOGGER.error("Triton inference failed for model '%s': %s", model, e)
            raise

        decoded: Dict[str, Any] = {}
        for name in outputs:
            try:
                raw = result.as_numpy(name)
                decoded[name] = decode_infer_output(raw) if decode else raw
            except Exception as e:
                LOGGER.warning("Failed to read output '%s' from Triton: %s", name, e)
                decoded[name] = None
        return decoded


def _reset_client_cache(url: Optional[str] = None) -> None:
    """Drop cached clients. Test-only helper.

    Args:
        url: Drop just this URL's client, or all of them when None.
    """
    if url is None:
        _clients.clear()
    else:
        _clients.pop(url, None)
