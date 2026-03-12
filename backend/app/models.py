from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(slots=True)
class ObservationRecord:
    id: str
    window_key: str
    window_title: str
    pid: int
    screenshot_path: str
    image_sha256: str
    markdown: str
    notes: str
    embedding: list[float]
    first_seen_at: datetime
    last_seen_at: datetime
    capture_count: int
    created_at: datetime
    updated_at: datetime


@dataclass(slots=True)
class SearchHit:
    record: ObservationRecord
    score: float
    vector_score: float
    keyword_score: float
