"""``api_type: typesafe | openrouter`` — the System One API over HTTP.

    POST {base_url}/systemone
    {"model": ..., "state": ..., "questions": {...}, "assets"?: {...}}

TypeSafe (``https://api.typesafe.ai/v1``) and OpenRouter
(``https://openrouter.ai/api/v1``) take the same body; OpenRouter adds
``usage.cost`` to the answer, and wraps a provider's error in its own
``{"error": {...}}``. Facts and probes: docs/JEV_PLAN.md.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, Mapping, Optional

import httpx

from operonx.core.exceptions import DecisionError
from operonx.core.policy import resource_retry, retry_call
from operonx.providers._utils.http import create_http_client
from operonx.providers.decisions.base import (
    BaseDecider,
    Decision,
    check_questions,
    normalize_answers,
    server_message,
)
from operonx.providers.decisions.config import RETRY_DEFAULTS, DecisionConfig

#: Header that raises the context from 64k to 262k tokens (TypeSafe docs, "models").
LONG_CONTEXT_HEADER = "TypeSafe-Danger-Zone-Long-Context"
#: Header a request with images needs (Jev 1.14; TypeSafe changelog 2026-10-14).
IMAGES_HEADER = "TypeSafe-Danger-Zone-Images"
#: In the body of the 400 a request over the context gets (probed 2026-11-23).
OVERFLOW_MARK = "max_tokens_exceeded"


class SystemOneDecider(BaseDecider):
    """One ``decision:`` resource on TypeSafe or OpenRouter."""

    def __init__(self, config: DecisionConfig, name: str = "") -> None:
        super().__init__(config, name)
        if not config.api_key:
            raise ValueError(
                f"decision resource {name or config.api_type.value!r} has no api_key "
                f"(api_key: ${{{_key_env(config)}}} in resources.yaml)"
            )
        self.url = f"{config.base_url.rstrip('/')}/systemone"
        self.retry = resource_retry(config, **RETRY_DEFAULTS)
        self._client: Optional[httpx.AsyncClient] = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = create_http_client(timeout=self.config.timeout)
        return self._client

    async def decide(
        self,
        state: Any,
        questions: Mapping[str, Mapping[str, Any]],
        assets: Optional[Mapping[str, Any]] = None,
    ) -> Decision:
        check_questions(questions, self.name)
        return await self._decide(state, dict(questions), dict(assets) if assets else None)

    async def _decide(
        self, state: Any, questions: Dict[str, Any], assets: Optional[Dict[str, Any]]
    ) -> Decision:
        try:
            return await self._ask(state, questions, assets)
        except DecisionError as e:
            if not (
                self.config.split_on_overflow
                and len(questions) > 1
                and e.status == 400
                and OVERFLOW_MARK in str(e)
            ):
                raise
        # Over the context with several questions: each half is a shorter
        # request. One question over it means the state itself is too long.
        keys = list(questions)
        half = len(keys) // 2
        first, second = await asyncio.gather(
            self._decide(state, {k: questions[k] for k in keys[:half]}, assets),
            self._decide(state, {k: questions[k] for k in keys[half:]}, assets),
        )
        return first.merged(second)

    async def _ask(
        self, state: Any, questions: Dict[str, Any], assets: Optional[Dict[str, Any]]
    ) -> Decision:
        body: Dict[str, Any] = {"model": self.config.model, "state": state, "questions": questions}
        if assets:
            body["assets"] = assets
        data = await retry_call(
            lambda: self._post(body, questions, images=bool(assets)),
            self.retry,
            label=f"decision:{self.name}" if self.name else "decision",
        )
        usage = data.get("usage") if isinstance(data.get("usage"), Mapping) else {}
        tokens_in = _int(usage.get("input_tokens"))
        tokens_out = _int(usage.get("output_tokens"))
        return Decision(
            answers=normalize_answers(data.get("answers"), questions, self.name),
            usage={
                "prompt_tokens": tokens_in,
                "completion_tokens": tokens_out,
                "total_tokens": tokens_in + tokens_out,
            },
            cost_usd=self._cost(usage, tokens_in),
            model=data.get("model") if isinstance(data.get("model"), str) else self.config.model,
        )

    async def _post(
        self, body: Dict[str, Any], questions: Dict[str, Any], images: bool
    ) -> Dict[str, Any]:
        headers = {"Authorization": f"Bearer {self.config.api_key}"}
        if self.config.long_context:
            headers[LONG_CONTEXT_HEADER] = "true"
        if images:
            headers[IMAGES_HEADER] = "true"
        headers.update(self.config.headers or {})
        # A connection error or a timeout is raised as httpx raises it: it
        # is transient, here and to an op's retry= around this call.
        response = await self.client.post(self.url, json=body, headers=headers)
        status = response.status_code
        if status >= 400:
            hint = "; check the resource's api_key" if status in (401, 403) else ""
            raise DecisionError(
                f"HTTP {status} from {self.url}: {server_message(response.content)}{hint}",
                resource=self.name,
                questions=list(questions),
                status=status,
                response=response,
            )
        try:
            data = response.json()
        except ValueError as e:
            raise DecisionError(
                f"{self.url} answered {status} with no JSON; check the resource's base_url",
                resource=self.name,
                questions=list(questions),
                original_error=e,
            ) from e
        if not isinstance(data, dict):
            raise DecisionError(
                f"{self.url} answered a {type(data).__name__}, not an object; check base_url",
                resource=self.name,
            )
        return data

    def _cost(self, usage: Mapping[str, Any], tokens_in: int) -> Optional[float]:
        """The route's reported cost (OpenRouter), else the resource's price,
        else None: unknown, never 0. Output tokens are free."""
        reported = usage.get("cost")
        if isinstance(reported, (int, float)) and not isinstance(reported, bool):
            return float(reported)
        if self.config.cost_per_input_token is not None:
            return tokens_in * self.config.cost_per_input_token
        return None

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _key_env(config: DecisionConfig) -> str:
    return "OPENROUTER_API_KEY" if config.api_type.value == "openrouter" else "TYPESAFE_API_KEY"
