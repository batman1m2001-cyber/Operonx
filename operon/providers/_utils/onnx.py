"""Shared ONNX model-loading helper.

Centralises the ~35 lines of identical code that both ONNXEmbedding and
ONNXReranker use to load a session + tokenizer from a local model directory.
"""

from __future__ import annotations

from typing import Tuple

from operon.core import LOGGER


def load_onnx_session(model_path: str) -> Tuple[any, any, str]:
    """Load an ONNX inference session and tokenizer from a local directory.

    Args:
        model_path: Path to a local directory containing ``model.onnx`` and
            ``tokenizer.json``.

    Returns:
        ``(session, tokenizer, device_str)`` where *device_str* is ``"cuda"``
        or ``"cpu"``.  The tokenizer has padding and truncation (max 512)
        enabled.

    Raises:
        ValueError: If *model_path* is not an existing directory.
        FileNotFoundError: If ``model.onnx`` or ``tokenizer.json`` is missing.
        ImportError: If ``onnxruntime`` or ``tokenizers`` are not installed.
    """
    from pathlib import Path

    import onnxruntime as ort
    from tokenizers import Tokenizer

    path = Path(model_path)
    if not path.exists() or not path.is_dir():
        raise ValueError(f"Model path does not exist or is not a directory: {path}")

    LOGGER.info("Loading ONNX model from local path: %s", path)

    providers = ["CPUExecutionProvider"]
    if "CUDAExecutionProvider" in ort.get_available_providers():
        providers.insert(0, "CUDAExecutionProvider")
        device = "cuda"
        LOGGER.info("ONNX Runtime will use GPU (CUDA)")
    else:
        device = "cpu"
        LOGGER.info("ONNX Runtime will use CPU")

    onnx_model_path = path / "model.onnx"
    if not onnx_model_path.exists():
        raise FileNotFoundError(f"ONNX model file not found: {onnx_model_path}")

    session_options = ort.SessionOptions()
    session_options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    session = ort.InferenceSession(
        str(onnx_model_path), sess_options=session_options, providers=providers
    )

    tokenizer_path = path / "tokenizer.json"
    if not tokenizer_path.exists():
        raise FileNotFoundError(f"Tokenizer file not found: {tokenizer_path}")

    tokenizer = Tokenizer.from_file(str(tokenizer_path))
    tokenizer.enable_padding(pad_id=0, pad_token="[PAD]")
    tokenizer.enable_truncation(max_length=512)

    return session, tokenizer, device
