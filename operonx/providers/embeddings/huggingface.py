from __future__ import annotations

from functools import lru_cache
from typing import Any, Dict, List, Union

from operonx.providers._utils.huggingface import load_hf_model
from operonx.providers.embeddings.base import BaseEmbedder
from operonx.providers.embeddings.config import EmbeddingConfig


class HFEmbedding(BaseEmbedder):
    """HuggingFace embedding using Transformers.

    This embedder runs models locally using the transformers library.
    Requires: pip install transformers torch
    """

    __slots__ = ["config", "model", "tokenizer", "_output_dim"]

    def __init__(self, config: EmbeddingConfig) -> None:
        """Initialize the local embedding client with the provided configuration.

        Args:
            config: Configuration containing model name and dimensions

        Raises:
            ImportError: If transformers or torch is not installed
            ValueError: If model name is not provided
        """
        try:
            import torch  # noqa: F401
            from transformers import AutoModel  # noqa: F401
        except ImportError as e:
            raise ImportError(
                "transformers and torch are required for HFEmbedding. "
                "Install them with: pip install transformers torch"
            ) from e

        if not config.model:
            raise ValueError("model name is required in the configuration")

        self.config = config
        self._output_dim = None

        # Load model and tokenizer
        try:
            from transformers import AutoModel

            self.model, self.tokenizer = load_hf_model(config.model, AutoModel)
        except Exception as e:
            raise RuntimeError(f"Failed to load model '{config.model}': {str(e)}") from e

    def _mean_pooling(self, model_output, attention_mask):
        """Apply mean pooling to get sentence embeddings.

        Args:
            model_output: Output from the transformer model
            attention_mask: Attention mask for the input tokens

        Returns:
            Pooled embeddings
        """
        import torch

        token_embeddings = model_output[
            0
        ]  # First element of model_output contains all token embeddings
        input_mask_expanded = attention_mask.unsqueeze(-1).expand(token_embeddings.size()).float()
        return torch.sum(token_embeddings * input_mask_expanded, 1) / torch.clamp(
            input_mask_expanded.sum(1), min=1e-9
        )

    async def run(self, texts: Union[str, List[str]], **kwargs: Any) -> Dict[str, Any]:
        """Generate embeddings for the given texts.

        Args:
            texts: Single text string or list of text strings to embed
            **kwargs: Additional parameters (e.g., max_length, truncation)

        Returns:
            Dict with 'embeddings' key containing list of embedding vectors
        """
        import torch

        # Handle single string input
        if isinstance(texts, str):
            texts = [texts]

        if not isinstance(texts, list) or not all(isinstance(t, str) for t in texts):
            raise ValueError("Input must be a string or list of strings")

        try:
            # Tokenize sentences
            max_length = kwargs.get("max_length", 512)
            truncation = kwargs.get("truncation", True)

            encoded_input = self.tokenizer(
                texts,
                padding=True,
                truncation=truncation,
                max_length=max_length,
                return_tensors="pt",
            )

            # Move to same device as model
            device = next(self.model.parameters()).device
            encoded_input = {k: v.to(device) for k, v in encoded_input.items()}

            # Compute token embeddings
            with torch.no_grad():
                model_output = self.model(**encoded_input)

            # Perform pooling
            embeddings = self._mean_pooling(model_output, encoded_input["attention_mask"])

            # Normalize embeddings
            import torch.nn.functional as F

            embeddings = F.normalize(embeddings, p=2, dim=1)

            # Store output dimension
            if self._output_dim is None:
                self._output_dim = embeddings.shape[1]

            # Convert to list of lists
            embeddings_list = embeddings.cpu().tolist()

            return {"embeddings": embeddings_list}

        except Exception as e:
            raise RuntimeError(f"Failed to generate embeddings: {str(e)}") from e

    @lru_cache(maxsize=1)
    def get_output_dim(self) -> int:
        """Get the output dimension of the embeddings.

        Returns:
            The dimensionality of the embedding vectors for the current model.
        """
        if self._output_dim is not None:
            return self._output_dim

        # If not cached, compute it by running a test embedding
        if self.config.dimensions:
            return self.config.dimensions

        # Fallback: run a test to get dimension
        import asyncio

        test_result = asyncio.run(self.run("test"))
        return len(test_result["embeddings"][0])
