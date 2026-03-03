"""Configuration models for hush-serve."""

from typing import List, Optional

from pydantic import BaseModel, Field


class EndpointConfig(BaseModel):
    """Configuration for a single API endpoint."""

    path: str
    mode: str = "python"
    stream: Optional[bool] = None  # None = auto-detect
    batch: bool = True
    jobs: bool = False
    websocket: bool = False
    methods: List[str] = Field(default_factory=lambda: ["POST"])
    tags: List[str] = Field(default_factory=list)
    summary: str = ""
    max_batch_size: int = 100
    job_ttl_seconds: int = 3600
    ws_max_idle_seconds: float = 300.0
    batch_concurrency: int = 10


class AppConfig(BaseModel):
    """Application-level configuration."""

    title: str = "Hush API"
    description: str = ""
    version: str = "0.1.0"
    mode: str = "python"
    cors: bool = True
    cors_origins: List[str] = Field(default_factory=lambda: ["*"])
