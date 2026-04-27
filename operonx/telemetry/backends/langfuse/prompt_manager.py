"""LangfusePromptManager — prompt management via Langfuse SDK.

Separated from LangfuseClient so the core tracing path has zero SDK dependency.
Requires: pip install operonx-telemetry[langfuse]
"""

import logging
import os
from typing import Optional

from operonx.telemetry.backends.langfuse.config import LangfuseConfig

LOGGER = logging.getLogger("operonx.tracing")


class LangfusePromptManager:
    """Manages Langfuse prompts via the official SDK.

    Requires the ``langfuse`` package (``pip install operonx-telemetry[langfuse]``).

    Example:
        ```python
        from operonx.telemetry import LangfusePromptManager, LangfuseConfig

        pm = LangfusePromptManager(config=LangfuseConfig.from_env())
        text = pm.get_prompt_text("my-prompt")
        formatted = pm.format_prompt("my-prompt", name="Alice")

        # Bracket notation
        text = pm["my-prompt"]
        ```
    """

    def __init__(
        self,
        config: Optional[LangfuseConfig] = None,
        resource: Optional[str] = None,
    ):
        if config is None and resource is None:
            raise ValueError("Must provide either 'config' or 'resource'")
        if config is not None and resource is not None:
            raise ValueError("Cannot provide both 'config' and 'resource'")
        self._config = config
        self._resource = resource
        self._sdk_client = None

    def _get_config(self) -> LangfuseConfig:
        if self._config is not None:
            return self._config
        from operonx.core.registry import ResourceHub

        return ResourceHub.instance().get(self._resource).config

    @property
    def _langfuse(self):
        """Lazy initialization of Langfuse SDK client."""
        if self._sdk_client is None:
            from langfuse import Langfuse

            config = self._get_config()
            if config.no_proxy:
                os.environ["NO_PROXY"] = config.no_proxy

            self._sdk_client = Langfuse(
                public_key=config.public_key,
                secret_key=config.secret_key,
                host=config.host,
            )
        return self._sdk_client

    def get_prompt(self, name: str, version: Optional[int] = None, **kwargs):
        """Get a prompt object from Langfuse."""
        if version:
            return self._langfuse.get_prompt(name, version=version, **kwargs)
        return self._langfuse.get_prompt(name, **kwargs)

    def get_prompt_text(self, name: str, version: Optional[int] = None) -> str:
        """Get prompt text content."""
        prompt = self.get_prompt(name, version=version)
        return prompt.prompt

    def format_prompt(self, name: str, **variables) -> str:
        """Get and format a prompt with variables."""
        prompt_text = self.get_prompt_text(name)
        return prompt_text.format(**variables)

    def __getitem__(self, prompt_name: str) -> str:
        """Get prompt text using bracket notation: ``pm["my-prompt"]``."""
        return self.get_prompt_text(prompt_name)

    def __repr__(self) -> str:
        if self._resource:
            return f"<LangfusePromptManager resource={self._resource}>"
        return f"<LangfusePromptManager host={self._config.host}>"
