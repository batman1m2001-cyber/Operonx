# -*- coding: utf-8 -*-

from __future__ import annotations

from typing import Any, Dict, List

import numpy as np

from operon.providers._utils.onnx import load_onnx_session
from operon.providers.rerankers.config import RerankingConfig

from .base import BaseReranker


class ONNXReranker(BaseReranker):
    """ONNX-based reranker using ONNX Runtime.

    This reranker runs ONNX models locally using onnxruntime.
    Supports sequence classification models like BGE-reranker-v2-m3.

    Requires: pip install onnxruntime tokenizers
    """

    def __init__(self, config: RerankingConfig) -> None:
        """Initialize the ONNX reranker with the provided configuration.

        Args:
            config: Configuration containing model path

        Raises:
            ImportError: If onnxruntime or tokenizers is not installed
            ValueError: If model path is not provided
        """
        try:
            import onnxruntime  # noqa: F401
            from tokenizers import Tokenizer  # noqa: F401
        except ImportError as e:
            raise ImportError(
                "onnxruntime and tokenizers are required for ONNXReranker. "
                "Install them with: pip install onnxruntime tokenizers"
            ) from e

        if not config.model:
            raise ValueError("model path is required in the configuration")

        self.config = config

        # Load model and tokenizer
        try:
            self.session, self.tokenizer, self._device = load_onnx_session(config.model)
        except (ValueError, FileNotFoundError):
            raise
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
            # Create query-text pairs
            [[query, text] for text in texts]

            # Flatten pairs for tokenization
            flat_pairs = [f"{query} {text}" for text in texts]

            # Get parameters
            max_length = kwargs.get("max_length", 512)
            truncation = kwargs.get("truncation", True)

            # Update tokenizer settings
            if truncation:
                self.tokenizer.enable_truncation(max_length=max_length)
            else:
                self.tokenizer.no_truncation()

            # Tokenize pairs
            encodings = self.tokenizer.encode_batch(flat_pairs)

            # Extract input_ids and attention_mask
            input_ids = np.array([enc.ids for enc in encodings], dtype=np.int64)
            attention_mask = np.array([enc.attention_mask for enc in encodings], dtype=np.int64)

            # Prepare ONNX inputs
            onnx_inputs = {"input_ids": input_ids, "attention_mask": attention_mask}

            # Add token_type_ids if the model expects it
            input_names = [inp.name for inp in self.session.get_inputs()]
            if "token_type_ids" in input_names:
                token_type_ids = np.zeros_like(input_ids, dtype=np.int64)
                onnx_inputs["token_type_ids"] = token_type_ids

            # Run inference
            outputs = self.session.run(None, onnx_inputs)

            # The output should be logits [batch_size, num_classes] or [batch_size]
            logits = outputs[0]

            # Handle different output shapes
            if len(logits.shape) > 1:
                # If output is [batch_size, num_classes], take the positive class
                if logits.shape[1] == 1:
                    logits = logits[:, 0]
                else:
                    # For multi-class, typically take the last class or max
                    logits = logits[:, -1]

            # Apply sigmoid to normalize scores to [0, 1]
            scores = 1 / (1 + np.exp(-logits))

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


async def main() -> None:
    """Main function for testing and demonstration."""
    import time

    print("Starting ONNX reranking...")

    # Example configuration
    config = RerankingConfig(
        model="/path/to/your/onnx-model",  # Path to ONNX model
    )

    onnx_reranker = ONNXReranker(config)

    # Test texts
    texts = [
        # Topic 1: Technology and AI
        "AI và machine learning đang cách mạng hóa nhiều ngành công nghiệp.",
        # Topic 2: Food and cuisine
        "Bún chả Hà Nội là một trong những đặc sản được yêu thích nhất.",
        # Topic 3: Weather
        "Hôm nay trời nắng đẹp, nhiệt độ khoảng 28 độ C.",
        "Thời tiết hôm nay rất tốt cho việc đi chơi.",
        # Topic 4: Unrelated
        "Tôi thích đọc sách vào buổi tối trước khi ngủ.",
    ]

    # query = "Trí tuệ nhân tạo đang thay đổi cách chúng ta làm việc và sinh hoạt."
    query = "Phở là món ăn truyền thống nổi tiếng của Việt Nam."

    start_time = time.time()

    results = await onnx_reranker.run(query=query, texts=texts, top_k=3, threshold=0.0)

    print(f"\nQuery: {query}\n")
    print("Top reranked results:")
    for i, result in enumerate(results, 1):
        print(f"\n{i}. Index: {result['index']}, Score: {result['score']:.4f}")
        print(f"   Text: {texts[result['index']][:100]}...")

    print(f"\nTime taken: {time.time() - start_time:.2f} seconds")


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
