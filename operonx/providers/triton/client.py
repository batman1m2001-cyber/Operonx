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

import asyncio
import atexit
import logging
import random
import re
from typing import Any, Dict, List, Optional

from operonx.providers.triton.decode import decode_infer_output
from operonx.providers.triton.dtypes import numpy_to_triton_dtype, to_infer_array

LOGGER = logging.getLogger(__name__)

__all__ = ["TritonClient", "get_aio_grpcclient", "close_all"]


# Lazily imported ``tritonclient.grpc.aio`` module.
_aio_grpcclient = None

# Process-wide cache of TritonClient instances, keyed by ``(url, ssl)``.
# Holds the gRPC channel open across calls — see module docstring. The
# key carries ``ssl`` because a plaintext and a TLS channel to the same
# host:port are different connections, and returning the wrong one fails
# at handshake time rather than at config time.
_clients: Dict[tuple, "TritonClient"] = {}


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


#: gRPC codes worth another attempt. Each says "this request did not get
#: an answer", never "the server refused this request" — retrying an
#: INVALID_ARGUMENT or a NOT_FOUND only burns the deadline again.
_TRANSIENT_CODES = frozenset(
    {"DEADLINE_EXCEEDED", "UNAVAILABLE", "RESOURCE_EXHAUSTED", "ABORTED", "INTERNAL"}
)

_STATUS_RE = re.compile(r"StatusCode\.([A-Z_]+)")


def _status_code(exc: Exception) -> Optional[str]:
    """The gRPC status name for *exc*, if it carries one.

    `tritonclient` wraps the gRPC error in an `InferenceServerException`
    whose text embeds the code — `[StatusCode.DEADLINE_EXCEEDED] Deadline
    Exceeded`. The `.code()` accessor is tried first because a string
    match is a last resort, not a contract.
    """
    getter = getattr(exc, "code", None)
    if callable(getter):
        try:
            code = getter()
            name = getattr(code, "name", None)
            if isinstance(name, str):
                return name
        except Exception:  # noqa: BLE001 — fall through to the text
            pass
    match = _STATUS_RE.search(str(exc))
    return match.group(1) if match else None


def _is_transient(exc: Exception) -> bool:
    """Should this failure be tried again?

    Unknown shapes are **not** retried. A retry that cannot help still
    costs a full timeout, and doing that to an error nobody classified is
    how a 30s deadline becomes two minutes.
    """
    code = _status_code(exc)
    return code in _TRANSIENT_CODES if code else False


def close_all() -> None:
    """Close every cached channel, while grpc still has its globals.

    Registered with :mod:`atexit`, and that timing is the whole point.
    Left open, a channel is closed by ``AioChannel.__dealloc__`` during
    interpreter shutdown — which runs *after* ``grpc_aio`` has cleared
    its own module globals, so the teardown reaches for a ``POLLER`` that
    is already ``None``::

        Exception ignored in: 'grpc._cython.cygrpc.AioChannel.__dealloc__'
        AttributeError: 'NoneType' object has no attribute 'POLLER'

    Harmless — "Exception ignored" is the interpreter saying it already
    swallowed it, and the exit code is untouched — but it prints twice
    under the real result on every run that embedded anything, which
    reads as a failed run to everyone who sees it. `atexit` fires early
    enough that the channel is gone before that path is ever taken.

    Every failure here is swallowed on purpose. This function exists to
    remove noise at shutdown; it must not become a new source of it.
    """
    clients = list(_clients.values())
    _clients.clear()
    if not clients:
        return

    import asyncio

    for client in clients:
        try:
            closer = getattr(client.raw, "close", None)
            if closer is None:
                continue
            result = closer()
            if asyncio.iscoroutine(result):
                # At exit there is normally no loop left. A fresh one is
                # enough to drive `close()` to completion, and the channel
                # is released either way.
                try:
                    asyncio.run(result)
                except RuntimeError:
                    result.close()
        except Exception:  # noqa: BLE001 — shutdown is not a place to raise
            pass


atexit.register(close_all)


class TritonClient:
    """Async Triton gRPC client with dict-in / dict-out inference.

    Use :meth:`get` rather than the constructor so the gRPC channel is
    shared process-wide per URL.

    Attributes:
        url: Triton gRPC endpoint (``host:port``).
    """

    __slots__ = ("url", "ssl", "_raw")

    def __init__(self, url: str, ssl: bool = False):
        """Construct a client. Prefer :meth:`get` — see class docstring."""
        aio_grpc = get_aio_grpcclient()
        self.url = url
        self.ssl = ssl
        self._raw = aio_grpc.InferenceServerClient(url=url, ssl=ssl)

    @classmethod
    def get(cls, url: str, ssl: bool = False) -> "TritonClient":
        """Return the process-cached client for ``url``, creating it once.

        Args:
            url: Triton gRPC endpoint as ``host:port`` — **no scheme**.
                TLS is selected by ``ssl``, not by writing ``https://``.
            ssl: Open the channel with TLS. Required by endpoints served
                behind an ingress on 443.

        Returns:
            A shared :class:`TritonClient`. The same instance — and the
            same underlying gRPC channel — is returned for repeat calls
            with the same ``(url, ssl)``.
        """
        key = (url, bool(ssl))
        if key not in _clients:
            _clients[key] = cls(url, ssl=bool(ssl))
        return _clients[key]

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
        retries: int = 2,
        retry_base_delay: float = 0.5,
        retry_max_delay: float = 8.0,
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
            retries: Extra attempts on a *transient* transport failure —
                deadline, unavailable, resource-exhausted. Defaults to 2,
                because the failures this catches are one-offs: a cold
                channel's handshake, or a response that arrived while the
                event loop was busy. Set 0 to disable.
            retry_base_delay: First backoff, doubling per attempt.
            retry_max_delay: Ceiling on the backoff before jitter.

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
        #
        # Retried on transient transport codes only. The failure this
        # exists for is not a slow server: measured against a deployed
        # bge-m3, a full batch of sentences at five-way concurrency
        # answers in 5.5s against a 30s deadline. A deadline expires
        # anyway when the budget goes somewhere other than inference —
        # the TLS handshake on a cold channel (30x the steady-state
        # latency, once), or an event loop busy enough that the response
        # callback is not serviced before the clock runs out. Both
        # succeed on the next attempt, and nothing but a retry recovers
        # them: a longer timeout does not help when the time was never
        # spent on the model.
        last_error: Optional[Exception] = None
        for attempt in range(max(0, retries) + 1):
            try:
                result = await self._raw.infer(
                    model_name=model,
                    model_version=model_version,
                    inputs=infer_inputs,
                    outputs=infer_outputs,
                    client_timeout=timeout,
                )
                break
            except Exception as e:  # noqa: BLE001 — classified below
                last_error = e
                if attempt >= retries or not _is_transient(e):
                    LOGGER.error("Triton inference failed for model '%s': %s", model, e)
                    raise
                delay = min(retry_base_delay * (2**attempt), retry_max_delay)
                delay *= 0.5 + random.random()  # jitter; a burst retries together
                LOGGER.warning(
                    "Triton '%s' attempt %d/%d failed (%s) — retrying in %.1fs",
                    model,
                    attempt + 1,
                    retries + 1,
                    _status_code(e) or type(e).__name__,
                    delay,
                )
                await asyncio.sleep(delay)
        else:  # pragma: no cover — the loop always breaks or raises
            raise last_error  # type: ignore[misc]

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
