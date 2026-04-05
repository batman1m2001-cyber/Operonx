from __future__ import annotations

from functools import lru_cache
from typing import Any, Dict, List, Union

import numpy as np

from hush.providers._utils.onnx import load_onnx_session
from hush.providers.embeddings.base import BaseEmbedder
from hush.providers.embeddings.config import EmbeddingConfig


class ONNXEmbedding(BaseEmbedder):
    """ONNX-based embedding using ONNX Runtime.

    This embedder runs ONNX models locally using onnxruntime.
    Supports optional in-memory cache with binary file persistence.
    Cache format is compatible with the Rust OnnxEmbedder.

    Requires: pip install onnxruntime tokenizers
    """

    __slots__ = [
        "config",
        "session",
        "tokenizer",
        "_output_dim",
        "_device",
    ]

    def __init__(self, config: EmbeddingConfig) -> None:
        """Initialize the ONNX embedding client with the provided configuration.

        Args:
            config: Configuration containing model path and dimensions

        Raises:
            ImportError: If onnxruntime or tokenizers is not installed
            ValueError: If model path is not provided
        """
        try:
            import onnxruntime  # noqa: F401
            from tokenizers import Tokenizer  # noqa: F401
        except ImportError as e:
            raise ImportError(
                "onnxruntime and tokenizers are required for ONNXEmbedding. "
                "Install them with: pip install onnxruntime tokenizers"
            ) from e

        if not config.model:
            raise ValueError("model path is required in the configuration")

        self.config = config
        self._output_dim = None

        # Load model and tokenizer
        try:
            self.session, self.tokenizer, self._device = load_onnx_session(config.model)
        except (ValueError, FileNotFoundError):
            raise
        except Exception as e:
            raise RuntimeError(f"Failed to load model '{config.model}': {str(e)}") from e

        # Caching is handled at the op level (cache=True on EmbeddingOp)

    def _mean_pooling(self, token_embeddings: np.ndarray, attention_mask: np.ndarray) -> np.ndarray:
        """Apply mean pooling to get sentence embeddings.

        Args:
            token_embeddings: Token embeddings from the model [batch_size, seq_len, hidden_dim]
            attention_mask: Attention mask for the input tokens [batch_size, seq_len]

        Returns:
            Pooled embeddings [batch_size, hidden_dim]
        """
        # Expand attention mask to match token embeddings shape
        input_mask_expanded = np.expand_dims(attention_mask, axis=-1).astype(np.float32)
        input_mask_expanded = np.broadcast_to(input_mask_expanded, token_embeddings.shape)

        # Sum embeddings weighted by attention mask
        sum_embeddings = np.sum(token_embeddings * input_mask_expanded, axis=1)

        # Calculate sum of attention mask with minimum clamp
        sum_mask = np.clip(np.sum(input_mask_expanded, axis=1), a_min=1e-9, a_max=None)

        # Mean pooling
        return sum_embeddings / sum_mask

    def _normalize(self, embeddings: np.ndarray) -> np.ndarray:
        """Normalize embeddings using L2 normalization.

        Args:
            embeddings: Input embeddings [batch_size, hidden_dim]

        Returns:
            Normalized embeddings [batch_size, hidden_dim]
        """
        # Calculate L2 norm along the last dimension
        norms = np.linalg.norm(embeddings, ord=2, axis=1, keepdims=True)
        # Avoid division by zero
        norms = np.maximum(norms, 1e-12)
        return embeddings / norms

    async def run(self, texts: Union[str, List[str]], **kwargs: Any) -> Dict[str, Any]:
        """Generate embeddings for the given texts.

        Args:
            texts: Single text string or list of text strings to embed
            **kwargs: Additional parameters (e.g., max_length, truncation)

        Returns:
            Dict with 'embeddings' key containing list of embedding vectors
        """
        # Handle single string input
        if isinstance(texts, str):
            texts = [texts]

        if not isinstance(texts, list) or not all(isinstance(t, str) for t in texts):
            raise ValueError("Input must be a string or list of strings")

        try:
            # Get parameters
            max_length = kwargs.get("max_length", 512)
            truncation = kwargs.get("truncation", True)

            # Update tokenizer settings
            if truncation:
                self.tokenizer.enable_truncation(max_length=max_length)
            else:
                self.tokenizer.no_truncation()

            # Tokenize texts
            encodings = self.tokenizer.encode_batch(texts)

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

            # The first output should be the token embeddings (last_hidden_state)
            token_embeddings = outputs[0]

            # Perform mean pooling
            embeddings = self._mean_pooling(token_embeddings, attention_mask.astype(np.float32))

            # Normalize embeddings
            embeddings = self._normalize(embeddings)

            # Store output dimension
            if self._output_dim is None:
                self._output_dim = embeddings.shape[1]

            return {"embeddings": embeddings.tolist()}

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


async def main() -> None:
    """Main function for testing and demonstration with Vietnamese similarity."""
    import time

    print("Starting ONNX embedding generation with Vietnamese similarity test...")
    print("=" * 80)

    # Example configuration - update with your ONNX model path
    config = EmbeddingConfig(
        model="/path/to/your/onnx-model",  # Path to your ONNX model folder
        dimensions=1024,  # BGE-M3 has 1024 dimensions
    )

    onnx_embed = ONNXEmbedding(config)

    # Vietnamese test texts with different topics
    vietnamese_texts = [
        # Topic 1: Technology and AI
        "Trí tuệ nhân tạo đang thay đổi cách chúng ta làm việc và sinh hoạt.",
        "AI và machine learning đang cách mạng hóa nhiều ngành công nghiệp.",
        # Topic 2: Food and cuisine
        "Phở là món ăn truyền thống nổi tiếng của Việt Nam.",
        "Bún chả Hà Nội là một trong những đặc sản được yêu thích nhất.",
        # Topic 3: Weather
        "Hôm nay trời nắng đẹp, nhiệt độ khoảng 28 độ C.",
        "Thời tiết hôm nay rất tốt cho việc đi chơi.",
        # Topic 4: Unrelated
        "Tôi thích đọc sách vào buổi tối trước khi ngủ.",
    ]

    start_time = time.time()

    print(f"\nGenerating embeddings for {len(vietnamese_texts)} Vietnamese texts...")
    result = await onnx_embed.run(texts=vietnamese_texts)
    embeddings = result["embeddings"]

    print(f"✓ Generated {len(embeddings)} embeddings")
    print(f"✓ Embedding dimension: {len(embeddings[0])}")

    # Convert to numpy for easier computation
    embeddings_array = np.array(embeddings)

    # Calculate cosine similarity matrix
    def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
        """Calculate cosine similarity between two vectors."""
        return np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b))

    print("\n" + "=" * 80)
    print("COSINE SIMILARITY MATRIX")
    print("=" * 80)

    # Create similarity matrix
    n = len(embeddings)
    similarity_matrix = np.zeros((n, n))

    for i in range(n):
        for j in range(n):
            similarity_matrix[i][j] = cosine_similarity(embeddings_array[i], embeddings_array[j])

    # Print header
    print(f"\n{'Text':<60} | Similarities with other texts")
    print("-" * 80)

    # Print each text with its similarities
    for i, text in enumerate(vietnamese_texts):
        # Truncate text if too long
        text_display = text[:57] + "..." if len(text) > 60 else text
        print(f"\n[{i}] {text_display}")

        # Get similarities with other texts (excluding self)
        similarities = []
        for j in range(n):
            if i != j:
                similarities.append((j, similarity_matrix[i][j]))

        # Sort by similarity (descending)
        similarities.sort(key=lambda x: x[1], reverse=True)

        # Print top 3 most similar texts
        print("    Most similar to:")
        for rank, (j, sim) in enumerate(similarities[:3], 1):
            other_text = (
                vietnamese_texts[j][:50] + "..."
                if len(vietnamese_texts[j]) > 50
                else vietnamese_texts[j]
            )
            print(f"      {rank}. [{j}] (similarity: {sim:.4f}) {other_text}")

    # Print specific similarity comparisons
    print("\n" + "=" * 80)
    print("DETAILED SIMILARITY COMPARISONS")
    print("=" * 80)

    comparisons = [
        (0, 1, "AI/Technology related texts"),
        (2, 3, "Food/Cuisine related texts"),
        (4, 5, "Weather related texts"),
        (0, 2, "AI vs Food (different topics)"),
        (0, 6, "AI vs Reading books (different topics)"),
    ]

    for idx1, idx2, description in comparisons:
        sim = similarity_matrix[idx1][idx2]
        print(f"\n{description}:")
        print(f"  [{idx1}] {vietnamese_texts[idx1][:60]}...")
        print(f"  [{idx2}] {vietnamese_texts[idx2][:60]}...")
        print(f"  → Similarity: {sim:.4f}")

    print("\n" + "=" * 80)
    print(f"Total time taken: {time.time() - start_time:.2f} seconds")
    print("=" * 80)


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
