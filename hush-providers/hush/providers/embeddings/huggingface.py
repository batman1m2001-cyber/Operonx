from __future__ import annotations

from typing import Any, List, Optional, Union, Dict
from functools import lru_cache
from hush.providers.embeddings.base import BaseEmbedder
from hush.providers.embeddings.config import EmbeddingConfig


class HFEmbedding(BaseEmbedder):
    """HuggingFace embedding using Transformers.

    This embedder runs models locally using the transformers library.
    Requires: pip install transformers torch
    """

    __slots__ = ['config', 'model', 'tokenizer', '_output_dim']

    def __init__(self, config: EmbeddingConfig) -> None:
        """Initialize the local embedding client with the provided configuration.

        Args:
            config: Configuration containing model name and dimensions

        Raises:
            ImportError: If transformers or torch is not installed
            ValueError: If model name is not provided
        """
        try:
            from transformers import AutoTokenizer, AutoModel
            import torch
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
            import os
            from pathlib import Path

            # Check if model path is a local directory
            model_path = config.model
            is_local_path = os.path.exists(model_path) and os.path.isdir(model_path)

            if is_local_path:
                print(f"Loading model from local path: {model_path}")
            else:
                print(f"Loading model from HuggingFace Hub: {model_path}")

            # Load tokenizer and model from either local path or HuggingFace Hub
            self.tokenizer = AutoTokenizer.from_pretrained(
                model_path,
                local_files_only=is_local_path
            )
            self.model = AutoModel.from_pretrained(
                model_path,
                local_files_only=is_local_path
            )

            # Move model to GPU if available
            import torch
            if torch.cuda.is_available():
                self.model = self.model.cuda()
                print(f"Model loaded on GPU")
            else:
                print(f"Model loaded on CPU")

            # Set model to evaluation mode
            self.model.eval()

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

        token_embeddings = model_output[0]  # First element of model_output contains all token embeddings
        input_mask_expanded = attention_mask.unsqueeze(-1).expand(token_embeddings.size()).float()
        return torch.sum(token_embeddings * input_mask_expanded, 1) / torch.clamp(input_mask_expanded.sum(1), min=1e-9)

    async def run(
        self,
        texts: Union[str, List[str]],
        **kwargs: Any
    ) -> Dict[str, Any]:
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
            max_length = kwargs.get('max_length', 512)
            truncation = kwargs.get('truncation', True)

            encoded_input = self.tokenizer(
                texts,
                padding=True,
                truncation=truncation,
                max_length=max_length,
                return_tensors='pt'
            )

            # Move to same device as model
            device = next(self.model.parameters()).device
            encoded_input = {k: v.to(device) for k, v in encoded_input.items()}

            # Compute token embeddings
            with torch.no_grad():
                model_output = self.model(**encoded_input)

            # Perform pooling
            embeddings = self._mean_pooling(model_output, encoded_input['attention_mask'])

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


async def main() -> None:
    """Main function for testing and demonstration with Vietnamese similarity."""
    import numpy as np
    import time

    print("Starting HF embedding generation with Vietnamese similarity test...")
    print("=" * 80)

    # Example configuration - update with your ONNX model path
    config = EmbeddingConfig(
        model="BAAI/bge-m3",  # Path to your ONNX model folder
        dimensions=1024  # BGE-M3 has 1024 dimensions
    )

    hf_embed = HFEmbedding(config)

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
    result = await hf_embed.run(texts=vietnamese_texts)
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
        print(f"    Most similar to:")
        for rank, (j, sim) in enumerate(similarities[:3], 1):
            other_text = vietnamese_texts[j][:50] + "..." if len(vietnamese_texts[j]) > 50 else vietnamese_texts[j]
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
