from __future__ import annotations

from typing import Any, List, Dict, Union

import json
import aiohttp
import asyncio
from functools import lru_cache
from aiohttp.client_exceptions import ClientError
from hush.providers.embeddings.base import BaseEmbedder
from hush.providers.embeddings.config import EmbeddingConfig


class TEIEmbedding(BaseEmbedder):
    r"""Text Embedding Inference (TEI) client for generating embeddings via Hugging Face's serving framework.

    This class provides an interface to generate text embeddings using TEI servers, which can serve various
    embedding models deployed through Hugging Face's Text Embedding Inference framework.

    Args:
        config (EmbeddingConfig): Configuration object containing:
            - base_url: URL of the TEI server endpoint
            - api_key: Authentication token for the TEI service
            - api_type: Type of embedding model being served
            - dimensions: Output dimension of the embedding vectors
    """

    __slots__ = ['config', 'default_headers']

    def __init__(self, config: EmbeddingConfig) -> None:
        """Initialize the TEI embedding client with the provided configuration."""
        if not config.base_url:
            raise ValueError("base_url is required in the configuration")
        if not config.dimensions or config.dimensions <= 0:
            raise ValueError("dimensions must be a positive integer")

        self.config = config

        # Set up default headers
        self.default_headers = {
            'Content-Type': 'application/json'
        }
        if self.config.api_key:
            self.default_headers['Authorization'] = f'Bearer {self.config.api_key}'

    async def run(
        self,
        texts: Union[str, List[str]],
        **kwargs: Any
    ) -> Dict[str, Any]:
        """Generate embeddings for the given texts.

        Args:
            texts: Single text string or list of text strings to embed
            **kwargs: Additional parameters to pass to the embedding API

        Returns:
            Dict of List of embedding vectors, where each vector is a list of floats

        Raises:
            TEIConnectionError: If the connection to the TEI server fails
            TEIResponseError: If the response format is invalid
            ValueError: If the input format is invalid
        """
        # Handle single string input
        if isinstance(texts, str):
            texts = [texts]

        if not isinstance(texts, list) or not all(isinstance(t, str) for t in texts):
            raise ValueError("Input must be a string or list of strings")

        try:
            payload = json.dumps({"inputs": texts})

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self.config.base_url,
                    headers=self.default_headers,
                    data=payload,
                    timeout=aiohttp.ClientTimeout(total=30),  # Add reasonable timeout
                    **kwargs
                ) as response:
                    response.raise_for_status()
                    result = await response.json()
        except ClientError as e:
            raise ConnectionError(f"Failed to connect to TEI server: {str(e)}") from e
        except asyncio.TimeoutError as e:
            raise ConnectionError("Request timed out") from e

        try:
            if not isinstance(result, list):
                raise ValueError("Expected list response from API")

            # Validate and convert embeddings
            embeddings = []
            for embedding in result:
                if not isinstance(embedding, list):
                    raise ValueError("Invalid embedding format in response")
                # embeddings.append([float(x) for x in embedding])
                embeddings.append(embedding)
            return {"embeddings": embeddings}
        except (json.JSONDecodeError, ValueError) as e:
            raise ValueError(f"Invalid response format: {str(e)}") from e


    @lru_cache(maxsize=1)
    def get_output_dim(self) -> int:
        r"""Get the output dimension of the embeddings.

        Returns:
            The dimensionality of the embedding vectors for the current model.
        """
        return self.output_dim


import numpy as np
from typing import List, Tuple

def cosine_similarity_search(query_emb: List[float], embs: List[List[float]]) -> List[Tuple[int, float]]:
    """
    Calculate cosine similarity between a query embedding and a list of embeddings.

    Args:
        query_emb (List[float]): Query embedding vector
        embs (List[List[float]]): List of embedding vectors to compare against

    Returns:
        List[Tuple[int, float]]: List of tuples containing (index, similarity_score)
                                sorted by similarity score in descending order
    """
    # Convert inputs to numpy arrays for efficient computation
    query = np.array(query_emb)
    embeddings = np.array(embs)

    # Normalize the vectors
    query_norm = query / np.linalg.norm(query)
    embeddings_norm = embeddings / np.linalg.norm(embeddings, axis=1)[:, np.newaxis]

    # Calculate cosine similarity
    similarities = np.dot(embeddings_norm, query_norm)

    # Create list of (index, similarity) tuples
    results = [(idx, float(score)) for idx, score in enumerate(similarities)]

    # Sort by similarity score in descending order
    results.sort(key=lambda x: x[1], reverse=True)

    return results


async def main() -> None:
    """Main function for testing and demonstration."""
    import time

    start_time = time.time()
    print("Starting embedding generation...")

    tei_embed = TEIEmbedding(EmbeddingConfig(
        api_type = "tei",
        api_key = "your-api-key",
        base_url = "http://localhost:8080/embed",
        model = "BAAI/bge-m3",
        embed_batch_size = 32,
        dimensions = 1024
    ))

    text1 = """machine learning, deep learning, neural networks"""

    text2 = """natural language processing, text embeddings"""

    embs = await tei_embed.run(texts=[text1, text2])
    query_emb = await tei_embed.run(texts=["How to use the text embedding service"])

    results = cosine_similarity_search(query_emb["embeddings"][0], embs["embeddings"])

    # Print results
    for idx, score in results:
        print(f"Index: {idx}, Similarity Score: {score:.4f}")

    print(f"Completed in {time.time() - start_time:.2f}s")


if __name__ == "__main__":
    asyncio.run(main())
