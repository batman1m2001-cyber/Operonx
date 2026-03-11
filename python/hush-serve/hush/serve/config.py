"""Configuration models for hush-serve."""

from typing import List, Optional

from pydantic import BaseModel, Field


class EndpointConfig(BaseModel):
    """Configuration for a single API endpoint."""

    path: str
    stream: Optional[bool] = None  # None = auto-detect
    websocket: bool = False
    methods: List[str] = Field(default_factory=lambda: ["POST"])
    tags: List[str] = Field(default_factory=list)
    summary: str = ""
    ws_max_idle_seconds: float = 300.0


class AppConfig(BaseModel):
    """Application-level configuration."""

    title: str = "Hush API"
    description: str = ""
    version: str = "0.1.0"
    cors: bool = True
    cors_origins: List[str] = Field(default_factory=lambda: ["*"])
