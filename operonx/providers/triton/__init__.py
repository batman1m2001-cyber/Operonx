"""Low-level Triton Inference Server helpers.

Use these to write a bare ``@op`` against any Triton-hosted model::

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

``TritonClient`` is imported lazily so this package can be imported
without ``tritonclient[grpc]`` installed — the pure dtype/decode helpers
stay available either way.
"""

from operonx.providers.triton.decode import decode_infer_output, is_text_dtype
from operonx.providers.triton.dtypes import (
    DTYPE_MAP,
    numpy_to_triton_dtype,
    to_infer_array,
)

_LAZY = {
    "TritonClient": "operonx.providers.triton.client",
    "get_aio_grpcclient": "operonx.providers.triton.client",
}


def __getattr__(name: str):
    """Lazy attribute loading (PEP 562) for tritonclient-dependent symbols."""
    if name in _LAZY:
        import importlib

        module = importlib.import_module(_LAZY[name])
        obj = getattr(module, name)
        globals()[name] = obj  # cache for subsequent access
        return obj
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "TritonClient",
    "get_aio_grpcclient",
    "decode_infer_output",
    "is_text_dtype",
    "numpy_to_triton_dtype",
    "to_infer_array",
    "DTYPE_MAP",
]
