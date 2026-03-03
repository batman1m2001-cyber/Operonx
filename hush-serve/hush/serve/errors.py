"""Error response models and exception handlers."""

from typing import Optional

from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel


class ErrorResponse(BaseModel):
    """Standard error response body."""

    error: str
    detail: Optional[str] = None
    request_id: Optional[str] = None


async def workflow_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Handle uncaught exceptions from workflow execution."""
    request_id = getattr(request.state, "request_id", None)
    return JSONResponse(
        status_code=500,
        content=ErrorResponse(
            error="workflow_error",
            detail=str(exc),
            request_id=request_id,
        ).model_dump(),
    )


async def validation_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Handle input validation errors."""
    request_id = getattr(request.state, "request_id", None)
    return JSONResponse(
        status_code=422,
        content=ErrorResponse(
            error="validation_error",
            detail=str(exc),
            request_id=request_id,
        ).model_dump(),
    )
