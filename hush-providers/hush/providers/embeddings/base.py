from abc import ABC, abstractmethod
from typing import Any, Union, List, Dict


class BaseEmbedder(ABC):
    r"""Abstract base class for text embedding functionalities."""

    __slots__ = []  # Abstract base class with no instance attributes

    @abstractmethod
    async def run(
        self,
        texts: Union[str, List[str]],
        **kwargs: Any
    ) -> Dict[str, Any]:
        """
        Abstract method for embedding a list of text strings into a list of numerical vectors.

        Parameters:
            texts (list[str]): A list of text strings to be embedded.
            **kwargs (Any): Extra kwargs passed to the embedding API.

        Returns:
            Dict: A dict["embeddings"] contains the list that represents the
                generated embedding as a list of floating-point numbers.
        """
        pass

    def run_sync(
        self,
        texts: Union[str, List[str]],
        **kwargs: Any
    ) -> Dict[str, Any]:
        """
        Synchronous wrapper for the run method.

        This method allows calling the async run method from synchronous code
        by creating a new event loop or using the existing one.

        Parameters:
            texts (list[str]): A list of text strings to be embedded.
            **kwargs (Any): Extra kwargs passed to the embedding API.

        Returns:
            Dict: A dict["embeddings"] contains the list that represents the
                generated embedding as a list of floating-point numbers.
        """
        import asyncio

        try:
            # Try to get the current event loop
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # If loop is already running, we need to create a new thread
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as executor:
                    future = executor.submit(asyncio.run, self.run(texts, **kwargs))
                    return future.result()
            else:
                # Loop exists but not running, use it
                return loop.run_until_complete(self.run(texts, **kwargs))
        except RuntimeError:
            # No event loop exists, create one
            return asyncio.run(self.run(texts, **kwargs))

    @abstractmethod
    def get_output_dim(self) -> int:
        r"""Returns the output dimension of the embeddings.

        Returns:
            int: The dimensionality of the embeddings for the current model.
        """
        pass
