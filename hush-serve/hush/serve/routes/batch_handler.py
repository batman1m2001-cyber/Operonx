"""Batch processing handler: POST /path/batch -> [results]."""

from __future__ import annotations

import asyncio
import uuid
from typing import TYPE_CHECKING, Callable, List

from fastapi import Request
from pydantic import Field, create_model

if TYPE_CHECKING:
    from hush.serve.endpoint import Endpoint


def create_batch_handler(endpoint: "Endpoint") -> Callable:
    """Create a batch processing handler.

    Accepts a list of inputs and processes them concurrently
    with a configurable concurrency limit.
    """
    request_model = endpoint.request_model
    max_batch = endpoint.config.max_batch_size
    concurrency = endpoint.config.batch_concurrency

    batch_model = create_model(
        f"{request_model.__name__}Batch",
        items=(List[request_model], Field(..., max_length=max_batch)),
    )

    async def handler(body: batch_model, request: Request):
        semaphore = asyncio.Semaphore(concurrency)

        async def run_one(item):
            async with semaphore:
                result = await endpoint.engine.run(
                    inputs=item.model_dump(exclude_none=False),
                    request_id=str(uuid.uuid4()),
                    tracer=endpoint.tracer,
                )
                return {k: v for k, v in result.items() if not k.startswith("$")}

        results = await asyncio.gather(
            *[run_one(item) for item in body.items],
            return_exceptions=True,
        )

        output = []
        for r in results:
            if isinstance(r, Exception):
                output.append({"error": str(r)})
            else:
                output.append(r)

        return {"results": output, "total": len(output)}

    handler.__doc__ = f"Batch execute {endpoint.graph.name} workflow."
    return handler
