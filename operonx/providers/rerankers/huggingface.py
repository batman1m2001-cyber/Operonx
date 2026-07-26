# -*- coding: utf-8 -*-

from __future__ import annotations

from typing import Any, Dict, List

from operonx.providers._utils.huggingface import load_hf_model
from operonx.providers.rerankers.config import RerankingConfig

from .base import BaseReranker


class HFReranker(BaseReranker):
    """HuggingFace reranker using Transformers.

    This reranker runs models locally using the transformers library.
    Supports sequence classification models like BGE-reranker-v2-m3.

    Requires: pip install transformers torch
    """

    def __init__(self, config: RerankingConfig) -> None:
        """Initialize the HuggingFace reranker with the provided configuration.

        Args:
            config: Configuration containing model name

        Raises:
            ImportError: If transformers or torch is not installed
            ValueError: If model name is not provided
        """
        try:
            import torch  # noqa: F401
            from transformers import AutoModelForSequenceClassification  # noqa: F401
        except ImportError as e:
            raise ImportError(
                "transformers and torch are required for HFReranker. "
                "Install them with: pip install transformers torch"
            ) from e

        if not config.model:
            raise ValueError("model name is required in the configuration")

        self.config = config

        # Load model and tokenizer
        try:
            from transformers import AutoModelForSequenceClassification

            self.model, self.tokenizer = load_hf_model(
                config.model, AutoModelForSequenceClassification
            )
        except Exception as e:
            raise RuntimeError(f"Failed to load model '{config.model}': {str(e)}") from e

    async def run(
        self, query: str, texts: List[str], top_k: int = 3, threshold: float = 0.0, **kwargs: Any
    ) -> List[Dict]:
        """Rerank texts based on relevance to query.

        Args:
            query: The search query
            texts: List of texts to rerank
            top_k: Number of top results to return
            threshold: Minimum score threshold
            **kwargs: Additional parameters

        Returns:
            List of dicts with 'index' and 'score' keys, sorted by relevance
        """
        if not texts:
            return []

        try:
            import torch

            # Create query-text pairs
            pairs = [[query, text] for text in texts]

            # Tokenize pairs
            max_length = kwargs.get("max_length", 512)
            inputs = self.tokenizer(
                pairs, padding=True, truncation=True, return_tensors="pt", max_length=max_length
            )

            # Move to same device as model
            device = next(self.model.parameters()).device
            inputs = {k: v.to(device) for k, v in inputs.items()}

            # Get scores
            with torch.no_grad():
                logits = (
                    self.model(**inputs, return_dict=True)
                    .logits.view(
                        -1,
                    )
                    .float()
                )
                # Apply sigmoid to normalize scores to [0, 1]
                scores = torch.sigmoid(logits)

            # Convert to CPU and numpy
            scores = scores.cpu().numpy()

            # Create results with original indices
            results = [{"index": idx, "score": float(score)} for idx, score in enumerate(scores)]

            # Sort by score in descending order
            results.sort(key=lambda x: x["score"], reverse=True)

            # Apply threshold filter
            if threshold > 0.0:
                results = [r for r in results if r["score"] >= threshold]

            # Apply top_k limit
            if top_k is not None and top_k > 0:
                results = results[:top_k]

            return results

        except Exception as e:
            raise RuntimeError(f"Failed to rerank texts: {str(e)}") from e

    def run_sync(
        self, query: str, texts: List[str], top_k: int = 3, threshold: float = 0.0, **kwargs: Any
    ) -> List[Dict]:
        """Synchronous wrapper for the run method.

        Args:
            query: The search query
            texts: List of texts to rerank
            top_k: Number of top results to return
            threshold: Minimum score threshold
            **kwargs: Additional parameters

        Returns:
            List of dicts with 'index' and 'score' keys, sorted by relevance
        """
        import asyncio

        try:
            # Try to get the current event loop
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # If loop is already running, we need to create a new thread
                import concurrent.futures

                with concurrent.futures.ThreadPoolExecutor() as executor:
                    future = executor.submit(
                        asyncio.run, self.run(query, texts, top_k, threshold, **kwargs)
                    )
                    return future.result()
            else:
                # Loop exists but not running, use it
                return loop.run_until_complete(self.run(query, texts, top_k, threshold, **kwargs))
        except RuntimeError:
            # No event loop exists, create one
            return asyncio.run(self.run(query, texts, top_k, threshold, **kwargs))
