"""Triton inference-output decoding.

Pure functions, no I/O, no tritonclient dependency.

Triton returns byte-string tensors for text outputs (dtype kind ``S`` /
``U`` / ``O``). Callers almost always want a ``str`` — and for
single-element tensors, the scalar rather than a 1-element list. These
helpers implement that convention so every call site behaves the same.
"""

from typing import Any, Union

import numpy as np

__all__ = ["is_text_dtype", "decode_infer_output"]


def is_text_dtype(arr: np.ndarray) -> bool:
    """True when the array holds byte strings, unicode, or objects."""
    return arr.dtype.kind in ("S", "U", "O")


def decode_infer_output(raw: np.ndarray) -> Union[str, list, np.ndarray, Any]:
    """Decode one Triton output tensor into a Python-friendly value.

    Numeric tensors pass through as numpy arrays. Text tensors are
    decoded to ``str``; a single-element text tensor collapses to a bare
    string rather than a 1-element list, matching what callers expect
    from e.g. an ASR ``TRANSCRIPT`` output.

    Args:
        raw: Tensor from ``InferResult.as_numpy(name)``.

    Returns:
        ``str`` for single-element text tensors, ``list[str]`` for
        multi-element text tensors, the original array otherwise.
    """
    if not is_text_dtype(raw):
        return raw

    if raw.ndim == 0:
        item = raw.item()
        return item.decode("utf-8") if isinstance(item, bytes) else str(item)

    decoded = [v.decode("utf-8") if isinstance(v, bytes) else str(v) for v in raw.flat]
    return decoded[0] if len(decoded) == 1 else decoded
