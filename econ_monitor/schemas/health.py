"""Health check response schema."""

from datetime import datetime

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: str = Field(..., description="Overall service status ('ok' or 'degraded')")
    database: str = Field(
        ..., description="Database connectivity status ('connected' or 'disconnected')"
    )
    version: str = Field(default="0.1.0", description="API version")
    timestamp: datetime = Field(..., description="Current UTC timestamp")
