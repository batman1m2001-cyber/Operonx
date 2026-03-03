"""Background job handlers: POST /path/submit + GET /jobs/{job_id}."""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Callable

from fastapi import Request
from fastapi.responses import JSONResponse

from hush.serve.jobs import JobStatus, JobStore

if TYPE_CHECKING:
    from hush.serve.endpoint import Endpoint


def create_submit_handler(endpoint: "Endpoint", job_store: JobStore) -> Callable:
    """Create a handler that submits a workflow as a background job."""
    request_model = endpoint.request_model

    async def handler(body: request_model, request: Request):
        inputs = body.model_dump(exclude_none=False)
        job = await job_store.create(endpoint.config.path, inputs)

        async def run_job():
            await job_store.update(job.id, status=JobStatus.RUNNING)
            try:
                result = await endpoint.engine.run(
                    inputs=inputs,
                    request_id=job.id,
                    tracer=endpoint.tracer,
                )
                output = {k: v for k, v in result.items() if not k.startswith("$")}
                await job_store.update(
                    job.id,
                    status=JobStatus.COMPLETED,
                    result=output,
                    completed_at=time.time(),
                )
            except Exception as e:
                await job_store.update(
                    job.id,
                    status=JobStatus.FAILED,
                    error=str(e),
                    completed_at=time.time(),
                )

        asyncio.create_task(run_job())
        return {"job_id": job.id}

    handler.__doc__ = f"Submit {endpoint.graph.name} as background job."
    return handler


def create_job_status_handler(job_store: JobStore) -> Callable:
    """Create a handler to check job status."""

    async def handler(job_id: str):
        job = await job_store.get(job_id)
        if not job:
            return JSONResponse(status_code=404, content={"error": "Job not found"})

        return {
            "job_id": job.id,
            "status": job.status.value,
            "result": job.result,
            "error": job.error,
            "created_at": job.created_at,
            "completed_at": job.completed_at,
        }

    return handler
