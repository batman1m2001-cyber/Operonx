"""Batch Coordinator for managing OpenAI Batch API requests.

This module provides a BatchCoordinator that collects requests from multiple
LLM nodes and submits them as batch jobs to OpenAI's Batch API.
"""

import asyncio
import uuid
from typing import Dict, List, Any, Optional, TYPE_CHECKING
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

if TYPE_CHECKING:
    from hush.providers.llms.base import BaseLLM


class BatchStatus(Enum):
    """Status of a batch job."""
    PENDING = "pending"
    SUBMITTED = "submitted"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class BatchRequest:
    """A single request waiting to be batched."""
    request_id: str
    messages: List[dict]
    params: Dict[str, Any]
    future: asyncio.Future
    created_at: datetime = field(default_factory=datetime.now)


@dataclass
class BatchJob:
    """A batch job containing multiple requests."""
    job_id: str
    batch_id: Optional[str] = None  # OpenAI batch ID
    requests: List[BatchRequest] = field(default_factory=list)
    status: BatchStatus = BatchStatus.PENDING
    created_at: datetime = field(default_factory=datetime.now)
    completed_at: Optional[datetime] = None
    error: Optional[str] = None


class BatchCoordinator:
    """Coordinates batch requests across multiple LLM nodes.

    The coordinator collects requests from nodes that set batch_mode=True,
    groups them into batches, submits to OpenAI Batch API, and routes
    results back to the waiting callers.

    Example:
        ```python
        coordinator = BatchCoordinator(llm_backend)

        # From LLMNode with batch_mode=True
        result = await coordinator.submit(
            messages=[{"role": "user", "content": "Hello"}],
            temperature=0.7
        )
        ```
    """

    _instance: Optional['BatchCoordinator'] = None
    _coordinators: Dict[str, 'BatchCoordinator'] = {}

    def __init__(
        self,
        llm: 'BaseLLM',
        max_batch_size: int = 50000,  # OpenAI limit
        flush_interval: float = 60.0,  # Auto-flush after 60 seconds
        poll_interval: float = 30.0,
        timeout: float = 86400.0  # 24 hours
    ):
        """Initialize BatchCoordinator.

        Args:
            llm: The LLM backend to use for batch operations
            max_batch_size: Maximum requests per batch (OpenAI limit: 50000)
            flush_interval: Seconds before auto-flushing pending requests
            poll_interval: Seconds between status checks
            timeout: Maximum wait time for batch completion
        """
        self.llm = llm
        self.max_batch_size = max_batch_size
        self.flush_interval = flush_interval
        self.poll_interval = poll_interval
        self.timeout = timeout

        # Request queue
        self._pending_requests: List[BatchRequest] = []
        self._lock = asyncio.Lock()

        # Active batch jobs
        self._active_jobs: Dict[str, BatchJob] = {}

        # Background tasks
        self._flush_task: Optional[asyncio.Task] = None
        self._poll_tasks: Dict[str, asyncio.Task] = {}

    @classmethod
    def get_coordinator(
        cls,
        resource_key: str,
        llm: 'BaseLLM',
        max_batch_size: int = 50000,
        flush_interval: float = 60.0,
        poll_interval: float = 30.0,
        timeout: float = 86400.0
    ) -> 'BatchCoordinator':
        """Get or create a coordinator for a specific resource key.

        Args:
            resource_key: The LLM resource key
            llm: The LLM backend
            max_batch_size: Maximum requests per batch (OpenAI limit: 50000)
            flush_interval: Seconds before auto-flushing pending requests
            poll_interval: Seconds between status checks
            timeout: Maximum wait time for batch completion

        Returns:
            BatchCoordinator instance for this resource
        """
        if resource_key not in cls._coordinators:
            cls._coordinators[resource_key] = BatchCoordinator(
                llm=llm,
                max_batch_size=max_batch_size,
                flush_interval=flush_interval,
                poll_interval=poll_interval,
                timeout=timeout
            )
        return cls._coordinators[resource_key]

    async def submit(
        self,
        messages: List[dict],
        **params
    ) -> Any:
        """Submit a request for batch processing.

        This method queues the request and returns a future that will
        be resolved when the batch completes.

        Args:
            messages: Chat messages for this request
            **params: Additional parameters (temperature, max_tokens, etc.)

        Returns:
            ChatCompletion result when batch completes
        """
        loop = asyncio.get_event_loop()
        future = loop.create_future()

        request = BatchRequest(
            request_id=f"req-{uuid.uuid4().hex[:12]}",
            messages=messages,
            params=params,
            future=future
        )

        async with self._lock:
            self._pending_requests.append(request)

            # Start flush timer if not running
            if self._flush_task is None or self._flush_task.done():
                self._flush_task = asyncio.create_task(self._auto_flush())

            # Check if we should flush immediately
            if len(self._pending_requests) >= self.max_batch_size:
                await self._flush()

        # Wait for result
        return await future

    async def _auto_flush(self):
        """Auto-flush pending requests after interval."""
        await asyncio.sleep(self.flush_interval)
        async with self._lock:
            if self._pending_requests:
                await self._flush()

    async def _flush(self):
        """Flush pending requests into a batch job."""
        if not self._pending_requests:
            return

        # Take requests (up to max batch size)
        requests_to_batch = self._pending_requests[:self.max_batch_size]
        self._pending_requests = self._pending_requests[self.max_batch_size:]

        # Create batch job
        job = BatchJob(
            job_id=f"job-{uuid.uuid4().hex[:12]}",
            requests=requests_to_batch
        )
        self._active_jobs[job.job_id] = job

        # Submit batch asynchronously
        asyncio.create_task(self._process_batch(job))

    async def _process_batch(self, job: BatchJob):
        """Process a batch job: submit, poll, and resolve futures."""
        try:
            job.status = BatchStatus.SUBMITTED

            # Build batch requests for OpenAI
            batch_messages = [req.messages for req in job.requests]

            # Get common params from first request (they should be consistent)
            params = job.requests[0].params if job.requests else {}

            # Call the LLM's generate_batch method
            results = await self.llm.generate_batch(
                batch_messages=batch_messages,
                poll_interval=self.poll_interval,
                timeout=self.timeout,
                **params
            )

            job.status = BatchStatus.COMPLETED
            job.completed_at = datetime.now()

            # Resolve futures with results
            for request, result in zip(job.requests, results):
                if not request.future.done():
                    request.future.set_result(result)

        except Exception as e:
            job.status = BatchStatus.FAILED
            job.error = str(e)
            job.completed_at = datetime.now()

            # Reject all futures
            for request in job.requests:
                if not request.future.done():
                    request.future.set_exception(e)

        finally:
            # Cleanup
            if job.job_id in self._active_jobs:
                del self._active_jobs[job.job_id]

    async def flush_now(self):
        """Manually flush all pending requests immediately."""
        async with self._lock:
            await self._flush()

    def pending_count(self) -> int:
        """Get count of pending requests."""
        return len(self._pending_requests)

    def active_jobs_count(self) -> int:
        """Get count of active batch jobs."""
        return len(self._active_jobs)

    async def shutdown(self):
        """Shutdown coordinator and cancel pending operations."""
        # Cancel flush task
        if self._flush_task and not self._flush_task.done():
            self._flush_task.cancel()

        # Cancel poll tasks
        for task in self._poll_tasks.values():
            if not task.done():
                task.cancel()

        # Reject pending requests
        async with self._lock:
            for request in self._pending_requests:
                if not request.future.done():
                    request.future.set_exception(
                        RuntimeError("BatchCoordinator shutdown")
                    )
            self._pending_requests.clear()

    @classmethod
    async def shutdown_all(cls):
        """Shutdown all coordinators."""
        for coordinator in cls._coordinators.values():
            await coordinator.shutdown()
        cls._coordinators.clear()
