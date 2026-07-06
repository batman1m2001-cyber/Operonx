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


class PineconeRawResponse(BaseModel):
    """Internal model for Pinecone API response structure"""

    model: str
    data: List[Dict]
    usage: Dict


class VLLMRerankerResponse(BaseModel):
    """
    Pydantic model for reranker API response (compatible format)

    Attributes:
        id (str): Unique identifier for the reranking request
        results (List[RerankerResult]): List of reranked documents with their scores
    """

    id: str = Field(description="Unique identifier for the reranking request")
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


class PineconeReranker(BaseReranker):
    r"""Provides text reranking functionalities using Pinecone Rerank API."""

    def __init__(self, config: RerankingConfig) -> None:
        """Initialize the Pinecone reranker client with the provided configuration."""

        self.config = config

        # Set default base URL if not provided
        if not self.config.base_url:
            self.config.base_url = "https://api.pinecone.io/rerank"

        if not self.config.api_key:
            raise ValueError("api_key is required for Pinecone Reranker")

        # Set up Pinecone-specific headers
        self.default_headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Api-Key": self.config.api_key,
            "X-Pinecone-API-Version": "2025-04",
        }

    async def run(
        self,
        query: str,
        texts: List[str],
        top_k: int = None,
        threshold: float = None,
        return_documents: bool = True,
        truncate: str = "END",
        **kwargs: Any,
    ) -> List[Dict]:
        """
        Rerank documents using Pinecone Rerank API.

        Args:
            query (str): The query text to rerank documents against
            texts (List[str]): List of document texts to rerank
            top_k (int): Maximum number of results to return. Defaults to None (all results).
            threshold (float): Minimum score threshold. Defaults to None.
            return_documents (bool): Whether to return document content. Defaults to True.
            truncate (str): Truncation strategy ("END" or "NONE"). Defaults to "END".
            **kwargs: Additional arguments to pass to aiohttp session

        Returns:
            List[Dict]: List of reranked results with index and score (same format as VLLMReranker)
        """
        if not texts:
            return []

        try:
            # Prepare documents in Pinecone format
            documents = [{"id": f"doc_{i}", "text": text} for i, text in enumerate(texts)]

            # Build payload
            payload = {
                "model": self.config.model,
                "query": query,
                "documents": documents,
                "return_documents": return_documents,
                "top_n": top_k if top_k else len(texts),
                "parameters": {"truncate": truncate},
            }

            # Make async request
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self.config.base_url,
                    headers=self.default_headers,
                    data=json.dumps(payload),
                    timeout=aiohttp.ClientTimeout(total=30),
                    **kwargs,
                ) as response:
                    response.raise_for_status()
                    result = await response.json()

            # Convert Pinecone response to VLLMRerankerResponse format
            reranker_results = []
            for item in result.get("data", []):
                # Get the original text from the input texts using the index
                original_index = item["index"]
                original_text = texts[original_index]

                reranker_results.append(
                    RerankerResult(
                        index=original_index,
                        document=Document(text=original_text),
                        relevance_score=item["score"],
                    )
                )

            # Create compatible response object
            reranker_response = VLLMRerankerResponse(
                id=result.get("id", "pinecone-rerank"), results=reranker_results
            )

            # Export results in the same format as VLLMReranker
            return reranker_response.export(top_k=top_k, threshold=threshold, export_json=True)

        except ClientError as e:
            raise ConnectionError(f"Failed to connect to Pinecone server: {str(e)}") from e
        except asyncio.TimeoutError as e:
            raise ConnectionError("Request timed out") from e
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            raise ValueError(f"Invalid response format: {str(e)}") from e
