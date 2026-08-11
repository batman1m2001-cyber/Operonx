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
}


def numpy_to_triton_dtype(arr: np.ndarray) -> str:
    """Map a numpy array's dtype to its Triton dtype string.

    Args:
        arr: Array whose dtype to translate.

    Returns:
        Triton dtype string (e.g. ``"FP32"``).

    Raises:
        ValueError: If the dtype has no Triton equivalent in DTYPE_MAP.
    """
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
