# -*- coding: utf-8 -*-

from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List, Union

import aiohttp
from aiohttp.client_exceptions import ClientError
from pydantic import BaseModel, Field

from operon.providers.rerankers.config import RerankingConfig

from .base import BaseReranker


class RankingResult(BaseModel):
    """Single ranking result with index and score"""

    index: int = Field(description="Index of the ranked item")
    score: float = Field(description="Ranking score")


class RankingResults(BaseModel):
    """Collection of ranking results"""

    results: List[RankingResult]

    @classmethod
    def from_list(cls, data: List[Dict[str, Union[int, float]]]) -> "RankingResults":
        """Create from list of dicts with index and score"""
        return cls(results=[RankingResult(**item) for item in data])

    def filter(self, threshold: float = 0.0, top_k: int = None) -> List[RankingResult]:
        """Filter results by score threshold and/or top K.

        Args:
            threshold: Minimum score to include. Defaults to 0.0.
            top_k: Max number of results to return. Defaults to None.

        Returns:
            Filtered results sorted by score.
        """
        filtered = [r for r in self.results if r.score >= threshold]
        if top_k:
            filtered = filtered[:top_k]

        return [{"index": r.index, "score": r.score} for r in filtered]


class TEIReranker(BaseReranker):
    r"""Provides text reranking functionalities using Text Embedding Inference."""

    def __init__(self, config: RerankingConfig) -> None:
        """Initialize the TEI embedding client with the provided configuration."""
        if not config.base_url:
            raise ValueError("base_url is required in the configuration")

        self.config = config

        # Set up default headers
        self.default_headers = {"Content-Type": "application/json"}

        if self.config.api_key:
            self.default_headers["Authorization"] = f"Bearer {self.config.api_key}"

    async def run(
        self, query: str, texts: List[str], top_k: int = 3, threshold: float = 0.0, **kwargs: Any
    ) -> List[Dict]:
        if not texts:
            return []

        try:
            # 1. send request with input payload to get the ranked results
            payload = json.dumps({"query": query, "texts": texts, "raw_scores": False})

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self.config.base_url,
                    headers=self.default_headers,
                    data=payload,
                    timeout=aiohttp.ClientTimeout(total=30),
                    **kwargs,
                ) as response:
                    response.raise_for_status()
                    result = await response.json()

            rerank_response = RankingResults.from_list(result)

            return rerank_response.filter(threshold=threshold, top_k=top_k)

        except ClientError as e:
            raise ConnectionError(f"Failed to connect to TEI server: {str(e)}") from e
        except asyncio.TimeoutError as e:
            raise ConnectionError("Request timed out") from e
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            raise ValueError(f"Invalid response format: {str(e)}") from e


if __name__ == "__main__":
    # Example usage
    async def main():
        import time

        reranker = TEIReranker(RerankingConfig.default())

        query = "Thời gian xử lý và cung cấp giấy báo có cho giao dịch chuyển tiền nhanh 247 qua Napas là bao lâu?"

        start_time = time.time()

        results = await reranker.run(query=query, texts=[], top_k=20, threshold=0.0)

        for r in results:
            print(r)

        execution_time = time.time() - start_time
        print(f"\nExecution time: {execution_time:.4f} seconds")

    asyncio.run(main())
