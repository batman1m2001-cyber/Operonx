"""Langfuse client for ResourceHub integration."""

from typing import Optional

from hush.ops.backends.langfuse.config import LangfuseConfig


class LangfuseClient:
    """Enhanced Langfuse client with prompt management capabilities.

    This client wraps the Langfuse SDK and is registered to ResourceHub.
    It provides tracing, prompt management, and scoring functionality.

    Example:
        ```python
        from hush.core.registry import get_hub

        # Get client from ResourceHub
        client = get_hub().langfuse("default")

        # Create a trace
        trace = client.trace(name="my-workflow", user_id="user-123")

        # Get a prompt
        prompt = client.get_prompt("my-prompt")
        ```
    """

    def __init__(self, config: LangfuseConfig):
        """Initialize Langfuse client.

        Args:
            config: LangfuseConfig with API keys and host
        """
        self._config = config
        self._client = None

    @property
    def client(self):
        """Lazy initialization of Langfuse client."""
        if self._client is None:
            import os

            from langfuse import Langfuse

            # Set proxy bypass if configured
            if self._config.no_proxy:
                os.environ["NO_PROXY"] = self._config.no_proxy

            self._client = Langfuse(
                public_key=self._config.public_key,
                secret_key=self._config.secret_key,
                host=self._config.host,
            )
        return self._client

    @property
    def config(self) -> LangfuseConfig:
        """Get the configuration."""
        return self._config

    # Delegate common methods to underlying client

    def trace(self, **kwargs):
        """Create a new trace."""
        return self.client.trace(**kwargs)

    def span(self, **kwargs):
        """Create a new span."""
        return self.client.span(**kwargs)

    def generation(self, **kwargs):
        """Create a new generation."""
        return self.client.generation(**kwargs)

    def score(self, **kwargs):
        """Create a score."""
        return self.client.score(**kwargs)

    def flush(self):
        """Flush all pending events to Langfuse."""
        return self.client.flush()

    def get_prompt(
        self,
        name: str,
        version: Optional[int] = None,
        **kwargs,
    ):
        """Get a prompt from Langfuse.

        Args:
            name: Prompt name
            version: Optional specific version

        Returns:
            Prompt object from Langfuse
        """
        if version:
            return self.client.get_prompt(name, version=version, **kwargs)
        return self.client.get_prompt(name, **kwargs)

    def get_prompt_text(self, name: str, version: Optional[int] = None) -> str:
        """Get prompt text content.

        Args:
            name: Prompt name
            version: Optional specific version

        Returns:
            The prompt text content
        """
        prompt = self.get_prompt(name, version=version)
        return prompt.prompt

    def format_prompt(self, name: str, **variables) -> str:
        """Get and format a prompt with variables.

        Args:
            name: Prompt name
            **variables: Variables to format into the prompt

        Returns:
            Formatted prompt text
        """
        prompt_text = self.get_prompt_text(name)
        return prompt_text.format(**variables)

    def auth_check(self) -> bool:
        """Check authentication with Langfuse server.

        Returns:
            True if authentication is successful
        """
        return self.client.auth_check()

    def __getitem__(self, prompt_name: str) -> str:
        """Get prompt text using bracket notation.

        Args:
            prompt_name: The prompt name

        Returns:
            The prompt text content
        """
        return self.get_prompt_text(prompt_name)

    def __repr__(self) -> str:
        """String representation."""
        return f"<LangfuseClient host={self._config.host}>"
