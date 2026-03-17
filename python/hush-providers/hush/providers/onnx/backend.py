"""ONNX inference backend — session management + inference for arbitrary models."""

import logging
from pathlib import Path
from typing import List, Optional

import numpy as np

from hush.providers.onnx.config import OnnxInferenceConfig, OnnxInputType

logger = logging.getLogger(__name__)


class OnnxInferenceBackend:
    """Backend for running ONNX inference models.

    Supports:
    - MLP: per-embedding classification (batch, dim) → (batch,) logits
    - Attention: turn-sequence classification (1, T, dim) + role_ids + mask → (1,) logit
    """

    __slots__ = ["config", "session"]

    def __init__(self, config: OnnxInferenceConfig):
        self.config = config

        try:
            import onnxruntime as ort
        except ImportError as e:
            raise ImportError(
                "onnxruntime required for ONNX inference: pip install onnxruntime"
            ) from e

        model_path = Path(config.model_path)
        if not model_path.exists():
            raise FileNotFoundError(f"ONNX model not found: {model_path}")

        providers = ["CPUExecutionProvider"]
        if config.device == "cuda":
            providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]

        self.session = ort.InferenceSession(str(model_path), providers=providers)
        logger.info("Loaded ONNX model: %s (%s)", model_path.name, config.input_type.value)

    async def run(
        self,
        embeddings: List[List[float]],
        role_ids: Optional[List[int]] = None,
        mask: Optional[List[bool]] = None,
    ) -> dict:
        """Run inference on embeddings.

        Args:
            embeddings: List of embedding vectors.
            role_ids: Role IDs per turn (required for attention type).
            mask: Attention mask per turn (required for attention type).

        Returns:
            {"probabilities": [...]} for MLP (one per embedding)
            {"probability": float} for attention (one for the sequence)
        """
        if not embeddings:
            if self.config.input_type == OnnxInputType.MLP:
                return {"probabilities": []}
            return {"probability": 0.0}

        if self.config.input_type == OnnxInputType.MLP:
            return self._predict_mlp(embeddings)
        else:
            return self._predict_sequence(embeddings, role_ids or [], mask or [])

    def _predict_mlp(self, embeddings: List[List[float]]) -> dict:
        """MLP inference: (batch, dim) → probabilities."""
        arr = np.array(embeddings, dtype=np.float32)
        input_name = self.session.get_inputs()[0].name
        outputs = self.session.run(None, {input_name: arr})
        logits = outputs[0].flatten()
        probs = _sigmoid(logits).tolist()
        return {"probabilities": probs}

    def _predict_sequence(
        self,
        embeddings: List[List[float]],
        role_ids: List[int],
        mask: List[bool],
    ) -> dict:
        """Attention/Transformer inference: (1, T, dim) + role_ids + mask → probability."""
        n_turns = len(embeddings)

        emb_arr = np.array(embeddings, dtype=np.float32).reshape(1, n_turns, -1)
        role_arr = np.array(role_ids[:n_turns], dtype=np.int64).reshape(1, n_turns)
        mask_arr = np.array(mask[:n_turns], dtype=np.bool_).reshape(1, n_turns)

        outputs = self.session.run(
            None,
            {
                "embeddings": emb_arr,
                "role_ids": role_arr,
                "mask": mask_arr,
            },
        )
        logit = float(outputs[0].flatten()[0])
        prob = float(_sigmoid(np.array([logit]))[0])
        return {"probability": prob}


def _sigmoid(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-x))
