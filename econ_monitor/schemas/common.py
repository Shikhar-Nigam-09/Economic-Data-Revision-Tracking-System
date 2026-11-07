"""Standard API error and common response schemas."""

from typing import Any

from pydantic import BaseModel, Field


class ErrorDetail(BaseModel):
    code: str = Field(..., description="Machine-readable error classification code")
    message: str = Field(..., description="Human-readable error description")
    details: dict[str, Any] = Field(
        default_factory=dict, description="Additional context or validation details"
    )


class ErrorResponse(BaseModel):
    error: ErrorDetail
