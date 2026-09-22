"""Numpy ↔ Triton dtype translation.

Pure functions, no I/O, no tritonclient dependency — importable without
``pip install tritonclient[grpc]``.
"""

from typing import Any

import numpy as np

__all__ = ["DTYPE_MAP", "numpy_to_triton_dtype", "to_infer_array"]


# Numpy dtype → Triton dtype string.
DTYPE_MAP = {
    np.float32: "FP32",
    np.float64: "FP64",
    np.float16: "FP16",
    np.int32: "INT32",
    np.int64: "INT64",
    np.int16: "INT16",
    np.int8: "INT8",
    np.uint8: "UINT8",
    np.bool_: "BOOL",
    # Triton's BYTES covers both binary blobs and strings. A model taking
    # raw text declares a BYTES input, and the natural numpy carriers for
    # that are an object array of `bytes` (what you get from
    # `np.array([[t.encode()] for t in texts], dtype=object)`), a fixed-
    # width `bytes_` array, or a `str_` array. All three map here.
    np.object_: "BYTES",
    np.bytes_: "BYTES",
    np.str_: "BYTES",
}


#: dtype *kinds* that all mean BYTES on the wire. Matched before the
#: table because these dtypes are parameterised by width — ``|S1`` is not
#: ``==`` to ``np.bytes_``, so an equality lookup misses every fixed-width
#: string array and only catches ``object``.
_BYTES_KINDS = frozenset("OSU")


def numpy_to_triton_dtype(arr: np.ndarray) -> str:
    """Map a numpy array's dtype to its Triton dtype string.

    Args:
        arr: Array whose dtype to translate.

    Returns:
        Triton dtype string (e.g. ``"FP32"``, ``"BYTES"``).

    Raises:
        ValueError: If the dtype has no Triton equivalent.
    """
    if arr.dtype.kind in _BYTES_KINDS:
        return "BYTES"
    for np_dtype, triton_str in DTYPE_MAP.items():
        if arr.dtype == np_dtype:
            return triton_str
    raise ValueError(f"Unsupported numpy dtype: {arr.dtype}")


def to_infer_array(data: Any) -> np.ndarray:
    """Coerce arbitrary input data into a Triton-ready numpy array.

    Converts lists/scalars to numpy and promotes 0-d arrays to 1-d —
    Triton rejects rank-0 tensors.

    Args:
        data: numpy array, list, or scalar.

    Returns:
        A numpy array with ``ndim >= 1``.
    """
    if not isinstance(data, np.ndarray):
        data = np.array(data)
    if data.ndim == 0:
        data = data.reshape(1)
    return data
