"""In-memory background job store."""

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class Job:
    id: str
    endpoint_path: str
    inputs: Dict[str, Any]
    status: JobStatus = JobStatus.PENDING
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    completed_at: Optional[float] = None


class JobStore:
    """Thread-safe in-memory job store with TTL cleanup."""

    def __init__(self, ttl_seconds: int = 3600):
        self._jobs: Dict[str, Job] = {}
        self._lock = asyncio.Lock()
        self._ttl = ttl_seconds

    async def create(self, endpoint_path: str, inputs: dict) -> Job:
        async with self._lock:
            job = Job(id=str(uuid.uuid4()), endpoint_path=endpoint_path, inputs=inputs)
            self._jobs[job.id] = job
            self._cleanup_expired()
            return job

    async def get(self, job_id: str) -> Optional[Job]:
        async with self._lock:
            return self._jobs.get(job_id)

    async def update(self, job_id: str, **kwargs) -> None:
        async with self._lock:
            job = self._jobs.get(job_id)
            if job:
                for k, v in kwargs.items():
                    setattr(job, k, v)

    async def list_jobs(self, endpoint_path: Optional[str] = None) -> list:
        async with self._lock:
            jobs = list(self._jobs.values())
            if endpoint_path:
                jobs = [j for j in jobs if j.endpoint_path == endpoint_path]
            return jobs

    def _cleanup_expired(self):
        now = time.time()
        expired = [
            jid
            for jid, job in self._jobs.items()
            if job.completed_at and (now - job.completed_at) > self._ttl
        ]
        for jid in expired:
            del self._jobs[jid]
