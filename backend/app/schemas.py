from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class TimeRange(BaseModel):
    start: datetime | None = None
    end: datetime | None = None
    matched_text: str | None = None
    label: str | None = None
    query_without_time: str = ""
    source: str = "deterministic"


class ExtractTimeRequest(BaseModel):
    text: str = Field(default="")


class QueryRequest(BaseModel):
    query: str = Field(default="", description="The user's retrieval question.")
    time_filter: str | None = Field(
        default=None,
        description="Optional natural-language time filter such as 'yesterday afternoon'.",
    )
    limit: int = Field(default=8, ge=1, le=20)


class ObservationCard(BaseModel):
    id: str
    window_title: str
    pid: int
    first_seen_at: datetime
    last_seen_at: datetime
    capture_count: int
    screenshot_url: str
    markdown: str
    notes: str
    score: float | None = None
    vector_score: float | None = None
    keyword_score: float | None = None


class QueryResponse(BaseModel):
    answer: str
    cleaned_query: str
    extracted_time: TimeRange
    results: list[ObservationCard]


class CaptureStatusResponse(BaseModel):
    running: bool
    interval_seconds: int
    last_run_started_at: datetime | None = None
    last_run_completed_at: datetime | None = None
    last_error: str | None = None
    stats: dict
