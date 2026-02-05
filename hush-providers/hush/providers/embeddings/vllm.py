from __future__ import annotations

from typing import Any, List, Optional, Union, Dict
import time
import uuid
import json
import aiohttp
import asyncio
from functools import lru_cache
from aiohttp.client_exceptions import ClientError
from hush.providers.embeddings.base import BaseEmbedder


from pydantic import BaseModel, Field
from typing import List, Optional, Union
from numpy import array, ndarray

from hush.providers.embeddings.config import EmbeddingConfig


class EmbeddingData(BaseModel):
    """Single embedding result"""
    index: int
    object: str = Field(default="embedding")
    embedding: List[float]

    def to_numpy(self) -> ndarray:
        """Convert embedding to numpy array"""
        return array(self.embedding)


class UsageInfo(BaseModel):
    """Token usage information"""
    prompt_tokens: int
    total_tokens: int


class EmbeddingResponse(BaseModel):
    """Response from embedding API"""
    id: str = str(uuid.uuid4())
    object: str = Field(default="list")
    created: int = time.time()
    model: str
    data: List[EmbeddingData]
    usage: UsageInfo

    def embeddings(self, as_numpy: bool = False) -> Union[List[List[float]], List[ndarray]]:
        """Extract embeddings from response.

        Args:
            as_numpy (bool): Convert to numpy arrays. Defaults to False.

        Returns:
            List of embeddings as float lists or numpy arrays.
        """
        if as_numpy:
            return [d.to_numpy() for d in self.data]
        return [d.embedding for d in self.data]


class VLLMEmbedding(BaseEmbedder):

    __slots__ = ['config', 'default_headers']

    def __init__(self, config: EmbeddingConfig) -> None:
        """Initialize the VLLM embedding client with the provided configuration."""
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
        """
        # Handle single string input
        if isinstance(texts, str):
            texts = [texts]

        if not isinstance(texts, list) or not all(isinstance(t, str) for t in texts):
            raise ValueError("Input must be a string or list of strings")

        try:
            payload = json.dumps({"model": self.config.model, "input": texts})
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
                    embedding_response = EmbeddingResponse.model_validate(result)
                    return {"embeddings": embedding_response.embeddings()}
        except ClientError as e:
            raise ConnectionError(f"Failed to connect to VLLM server: {str(e)}") from e
        except asyncio.TimeoutError as e:
            raise ConnectionError("Request timed out") from e


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

    vllm_embed = VLLMEmbedding(EmbeddingConfig.default())

    text1 = """machine learning and deep learning"""
    text2 = """natural language processing"""

    embs = await vllm_embed.run(texts=[text1, text2])

    print(f"Generated embeddings: {embs}")
    print(f"Completed in {time.time() - start_time:.2f}s")


if __name__ == "__main__":
    asyncio.run(main())
