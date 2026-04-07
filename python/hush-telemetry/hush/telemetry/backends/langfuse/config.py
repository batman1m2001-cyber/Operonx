"""Langfuse configuration for ResourceHub."""

from typing import ClassVar, Optional

from hush.core.utils.yaml_model import YamlModel


class LangfuseConfig(YamlModel):
    """Configuration for Langfuse observability backend.

    This config is registered to ResourceHub and used to create LangfuseClient.

    Attributes:
        public_key: Public API key for Langfuse authentication
        secret_key: Secret API key for Langfuse authentication
        host: Langfuse server URL (default: cloud.langfuse.com)
        no_proxy: Proxy bypass setting for internal networks
        enabled: Whether tracing is enabled
        sample_rate: Sampling rate for traces (0.0 to 1.0)

    Example:
        ```yaml
        # resources.yaml
        langfuse:default:
          public_key: pk-...
          secret_key: sk-...
          host: https://cloud.langfuse.com
        ```

        ```python
        from hush.core.registry import get_hub

        client = get_hub().get("langfuse:default")
        ```
    """

    _category: ClassVar[str] = "langfuse"

    public_key: str
    secret_key: str
    host: str = "https://cloud.langfuse.com"
    no_proxy: Optional[str] = None
    enabled: bool = True
    sample_rate: float = 1.0

    @classmethod
    def from_env(cls) -> "LangfuseConfig":
        """Create config from environment variables.

        Environment variables:
            - LANGFUSE_PUBLIC_KEY
            - LANGFUSE_SECRET_KEY
            - LANGFUSE_HOST (optional)
            - NO_PROXY (optional)
        """
        import os

        return cls(
            public_key=os.environ["LANGFUSE_PUBLIC_KEY"],
            secret_key=os.environ["LANGFUSE_SECRET_KEY"],
            host=os.environ.get("LANGFUSE_HOST", "https://cloud.langfuse.com"),
            no_proxy=os.environ.get("NO_PROXY"),
        )
