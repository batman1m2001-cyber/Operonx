from abc import ABC, abstractmethod
from typing import List, Dict


class BaseReranker(ABC):
    r"""Abstract base class for reranking functionalities."""
    
    @abstractmethod
    async def run(
        query: str,
        texts: List[str],
        top_k: int = 3,
        threshold: float = 0.0,
        **kwargs
    ) -> List[Dict]:
        """Rerank a list of texts based on their relevance to the input query.

        Args:
            query (str): The search query used to rank the texts.
            texts (List[str]): A list of text strings to be reranked.

        Returns:
            List[Dict]: A list of dictionaries containing the reranked texts and their
                       associated metadata (e.g., scores, positions).
        
        Note:
            Implementation classes should specify the exact structure of the returned
            dictionaries in their own docstrings.
        """
        pass