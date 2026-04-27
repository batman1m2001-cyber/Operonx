"""Shared HuggingFace model-loading helper.

Centralises the ~30 lines of identical code that both HFEmbedding and
HFReranker use to load a model + tokenizer from a local path or Hub.
"""

from __future__ import annotations

from typing import Any, Tuple

from operonx.core import LOGGER


def load_hf_model(model_path: str, model_cls: Any) -> Tuple[Any, Any]:
    """Load a HuggingFace model and AutoTokenizer.

    Args:
        model_path: Local directory path or HuggingFace Hub model name.
        model_cls: Transformers model class to instantiate
            (e.g. ``AutoModel``, ``AutoModelForSequenceClassification``).

    Returns:
        ``(model, tokenizer)`` — model is in ``eval()`` mode and moved to
        GPU if CUDA is available.

    Raises:
        ImportError: If ``transformers`` or ``torch`` are not installed.
        RuntimeError: If the model or tokenizer cannot be loaded.
    """
    import os

    import torch
    from transformers import AutoTokenizer

    is_local = os.path.isdir(model_path)
    if is_local:
        LOGGER.info("Loading model from local path: %s", model_path)
    else:
        LOGGER.info("Loading model from HuggingFace Hub: %s", model_path)

    tokenizer = AutoTokenizer.from_pretrained(model_path, local_files_only=is_local)
    model = model_cls.from_pretrained(model_path, local_files_only=is_local)

    if torch.cuda.is_available():
        model = model.cuda()
        LOGGER.info("Model loaded on GPU")
    else:
        LOGGER.info("Model loaded on CPU")

    return model.eval(), tokenizer
