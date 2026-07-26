# -*- coding: utf-8 -*-

from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List

import aiohttp
from aiohttp.client_exceptions import ClientError
from pydantic import BaseModel, Field

from operonx.providers.rerankers.config import RerankingConfig

from .base import BaseReranker


class Usage(BaseModel):
    total_tokens: int


class Document(BaseModel):
    text: str


class RerankerResult(BaseModel):
    index: int
    document: Document
    relevance_score: float = Field(description="Relevance score for the document")


class VLLMRerankerResponse(BaseModel):
    """
    Pydantic model for VLLM reranker API response

    Attributes:
        id (str): Unique identifier for the reranking request
        model (str): Model used for reranking
        usage (Usage): Token usage information
        results (List[RerankerResult]): List of reranked documents with their scores
    """

    id: str = Field(description="Unique identifier for the reranking request")
    # model: str = Field(description="Model used for reranking")
    # usage: Usage
    results: List[RerankerResult]

    def export(
        self, top_k: int = None, threshold: float = None, export_json=True
    ) -> List[RerankerResult]:
        """Filter and optionally export results to JSON format.

        Args:
            top_k (int): Maximum number of results to return. Defaults to 3.
            threshold (float): Minimum score threshold. Defaults to 0.0.
            export_json (bool): Whether to return dict format. Defaults to True.

        Returns:
            Union[List[Dict[str, Union[int, float]]], List[RerankerResult]]:
                Filtered results in dict or model format.
        """
        top_k_results = self.results[:top_k] if top_k else self.results
        threshold_results = (
            [r for r in top_k_results if r.relevance_score > threshold]
            if threshold
            else top_k_results
        )

        if export_json:
            return [{"index": r.index, "score": r.relevance_score} for r in threshold_results]
        else:
            return threshold_results


class VLLMReranker(BaseReranker):
    r"""Provides text reranking functionalities using VLLM Embedding Inference."""

    def __init__(self, config: RerankingConfig) -> None:
        """Initialize the VLLM embedding client with the provided configuration."""

        self.config = config

        if not self.config.base_url:
            raise ValueError("base_url is required in the configuration")

        # Set up default headers
        self.default_headers = {"Content-Type": "application/json"}

        if self.config.api_key:
            self.default_headers["Authorization"] = f"Bearer {self.config.api_key}"

    async def run(
        self,
        query: str,
        texts: List[str],
        top_k: int = None,
        threshold: float = None,
        **kwargs: Any,
    ) -> List[Dict]:
        if not texts:
            return []

        try:
            # 1. send request with input payload to get the ranked results
            payload = json.dumps({"model": self.config.model, "query": query, "documents": texts})

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

            reranker_response = VLLMRerankerResponse.model_validate(result)

            return reranker_response.export(top_k=top_k, threshold=threshold, export_json=True)

        except ClientError as e:
            raise ConnectionError(f"Failed to connect to VLLM server: {str(e)}") from e
        except asyncio.TimeoutError as e:
            raise ConnectionError("Request timed out") from e
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            raise ValueError(f"Invalid response format: {str(e)}") from e
