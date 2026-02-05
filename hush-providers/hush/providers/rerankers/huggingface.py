# -*- coding: utf-8 -*-

from __future__ import annotations
from typing import Any, List, Dict

from hush.providers.rerankers.config import RerankingConfig
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
            from transformers import AutoModelForSequenceClassification, AutoTokenizer
            import torch
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
            import os

            # Check if model path is a local directory
            model_path = config.model
            is_local_path = os.path.exists(model_path) and os.path.isdir(model_path)

            if is_local_path:
                print(f"Loading reranker model from local path: {model_path}")
            else:
                print(f"Loading reranker model from HuggingFace Hub: {model_path}")

            # Load tokenizer and model
            self.tokenizer = AutoTokenizer.from_pretrained(
                model_path,
                local_files_only=is_local_path
            )
            self.model = AutoModelForSequenceClassification.from_pretrained(
                model_path,
                local_files_only=is_local_path
            )

            # Move model to GPU if available
            import torch
            if torch.cuda.is_available():
                self.model = self.model.cuda()
                print(f"Reranker model loaded on GPU")
            else:
                print(f"Reranker model loaded on CPU")

            # Set model to evaluation mode
            self.model.eval()

        except Exception as e:
            raise RuntimeError(f"Failed to load model '{config.model}': {str(e)}") from e

    async def run(
        self,
        query: str,
        texts: List[str],
        top_k: int = 3,
        threshold: float = 0.0,
        **kwargs: Any
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
            max_length = kwargs.get('max_length', 512)
            inputs = self.tokenizer(
                pairs,
                padding=True,
                truncation=True,
                return_tensors='pt',
                max_length=max_length
            )

            # Move to same device as model
            device = next(self.model.parameters()).device
            inputs = {k: v.to(device) for k, v in inputs.items()}

            # Get scores
            with torch.no_grad():
                logits = self.model(**inputs, return_dict=True).logits.view(-1, ).float()
                # Apply sigmoid to normalize scores to [0, 1]
                scores = torch.sigmoid(logits)

            # Convert to CPU and numpy
            scores = scores.cpu().numpy()

            # Create results with original indices
            results = [
                {"index": idx, "score": float(score)}
                for idx, score in enumerate(scores)
            ]

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
        self,
        query: str,
        texts: List[str],
        top_k: int = 3,
        threshold: float = 0.0,
        **kwargs: Any
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
                        asyncio.run,
                        self.run(query, texts, top_k, threshold, **kwargs)
                    )
                    return future.result()
            else:
                # Loop exists but not running, use it
                return loop.run_until_complete(
                    self.run(query, texts, top_k, threshold, **kwargs)
                )
        except RuntimeError:
            # No event loop exists, create one
            return asyncio.run(self.run(query, texts, top_k, threshold, **kwargs))


async def main() -> None:
    """Main function for testing and demonstration."""
    import asyncio
    import time

    print("Starting local reranking...")

    # Example configuration
    config = RerankingConfig(
        model="BAAI/bge-reranker-v2-m3",  # Or use local path like "bge-reranker-v2-m3"
    )

    hf_reranker = HFReranker(config)

    # Test texts
    texts = [
        "hi",
        "The giant panda (Ailuropoda melanoleuca), sometimes called a panda bear or simply panda, is a bear species endemic to China.",
        """File tài liệu: Quy định về giao dịch chuyển tiền nhanh 247
Câu hỏi: Thời gian xử lý tra soát giao dịch chuyển tiền nhanh 247 qua Napas là bao lâu?
Câu trả lời: Tối đa 04 ngày làm việc (không tính thứ 7, chủ nhật và ngày nghỉ lễ)""",

        """File tài liệu: Hướng dẫn giao dịch ngân hàng điện tử
Câu hỏi: Phí giao dịch chuyển tiền nhanh 247 là bao nhiêu?
Câu trả lời: Miễn phí cho giao dịch dưới 500.000 VND, 11.000 VND cho giao dịch trên 500.000 VND""",

        """File tài liệu: Quy định về bằng chứng giao dịch
Câu hỏi: Giấy báo có được cung cấp như thế nào?
Câu trả lời: Được cung cấp qua email trong vòng 04 ngày làm việc sau khi nhận yêu cầu"""
    ]

    query = "what is panda?"

    start_time = time.time()

    results = await hf_reranker.run(
        query=query,
        texts=texts,
        top_k=3,
        threshold=0.0
    )

    print(f"\nQuery: {query}\n")
    print("Top reranked results:")
    for i, result in enumerate(results, 1):
        print(f"\n{i}. Index: {result['index']}, Score: {result['score']:.4f}")
        print(f"   Text: {texts[result['index']][:100]}...")

    print(f"\nTime taken: {time.time() - start_time:.2f} seconds")


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
