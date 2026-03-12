from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Annotated

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .capture_service import CaptureService
from .config import get_settings
from .database import Database
from .llm import LLMService
from .retrieval import hybrid_retrieve
from .schemas import (
    CaptureStatusResponse,
    ExtractTimeRequest,
    ObservationCard,
    QueryRequest,
    QueryResponse,
    TimeRange,
)
from .time_filter import extract_time_range

load_dotenv()
settings = get_settings()
database = Database(settings.db_path)
llm_service = LLMService(settings)
capture_service = CaptureService(settings, database, llm_service)
static_dir = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(_: FastAPI):
    database.initialize()
    if settings.capture_enabled_on_startup:
        capture_service.start()
    yield
    capture_service.stop()


app = FastAPI(title=settings.app_name, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
app.mount("/captures", StaticFiles(directory=str(settings.screenshot_dir)), name="captures")


def _to_card(record, *, score=None, vector_score=None, keyword_score=None) -> ObservationCard:
    screenshot_url = "/captures/" + record.screenshot_path.replace("\\", "/")
    return ObservationCard(
        id=record.id,
        window_title=record.window_title,
        pid=record.pid,
        first_seen_at=record.first_seen_at,
        last_seen_at=record.last_seen_at,
        capture_count=record.capture_count,
        screenshot_url=screenshot_url,
        markdown=record.markdown,
        notes=record.notes,
        score=score,
        vector_score=vector_score,
        keyword_score=keyword_score,
    )


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(static_dir / "index.html")


@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True, "app": settings.app_name}


@app.get("/api/status", response_model=CaptureStatusResponse)
def get_status() -> CaptureStatusResponse:
    status = capture_service.status()
    return CaptureStatusResponse(
        running=status["running"],
        interval_seconds=settings.capture_interval_seconds,
        last_run_started_at=status["last_run_started_at"],
        last_run_completed_at=status["last_run_completed_at"],
        last_error=status["last_error"],
        stats=status["stats"],
    )


@app.post("/api/capture/start", response_model=CaptureStatusResponse)
def start_capture() -> CaptureStatusResponse:
    status = capture_service.start()
    return CaptureStatusResponse(
        running=status["running"],
        interval_seconds=settings.capture_interval_seconds,
        last_run_started_at=status["last_run_started_at"],
        last_run_completed_at=status["last_run_completed_at"],
        last_error=status["last_error"],
        stats=status["stats"],
    )


@app.post("/api/capture/stop", response_model=CaptureStatusResponse)
def stop_capture() -> CaptureStatusResponse:
    status = capture_service.stop()
    return CaptureStatusResponse(
        running=status["running"],
        interval_seconds=settings.capture_interval_seconds,
        last_run_started_at=status["last_run_started_at"],
        last_run_completed_at=status["last_run_completed_at"],
        last_error=status["last_error"],
        stats=status["stats"],
    )


@app.post("/api/capture/run-once", response_model=CaptureStatusResponse)
def run_capture_once() -> CaptureStatusResponse:
    status = capture_service.run_once()
    return CaptureStatusResponse(
        running=status["running"],
        interval_seconds=settings.capture_interval_seconds,
        last_run_started_at=status["last_run_started_at"],
        last_run_completed_at=status["last_run_completed_at"],
        last_error=status["last_error"],
        stats=status["stats"],
    )


@app.get("/api/observations", response_model=list[ObservationCard])
def list_observations(
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    start: datetime | None = None,
    end: datetime | None = None,
) -> list[ObservationCard]:
    rows = database.list_observations(limit=limit, start=start, end=end)
    return [_to_card(row) for row in rows]


@app.post("/api/tools/extract-time", response_model=TimeRange)
def preview_time_filter(request: ExtractTimeRequest) -> TimeRange:
    return extract_time_range(request.text, timezone_name=settings.timezone)


@app.post("/api/query", response_model=QueryResponse)
def query_history(request: QueryRequest) -> QueryResponse:
    if not request.query.strip() and not (request.time_filter or "").strip():
        raise HTTPException(status_code=400, detail="Provide a query or a time filter.")

    query_time = extract_time_range(request.query, timezone_name=settings.timezone)
    explicit_time = (
        extract_time_range(request.time_filter or "", timezone_name=settings.timezone)
        if request.time_filter
        else None
    )

    extracted_time = explicit_time if explicit_time and (explicit_time.start or explicit_time.end) else query_time
    cleaned_query = query_time.query_without_time.strip() or request.query.strip()

    hits = hybrid_retrieve(
        database=database,
        llm=llm_service,
        query=cleaned_query,
        extracted_time=extracted_time,
        limit=min(request.limit, settings.max_query_results),
    )

    try:
        answer = llm_service.answer_question(
            user_query=request.query.strip() or "What was on screen during this period?",
            cleaned_query=cleaned_query,
            extracted_time=extracted_time,
            hits=hits,
        )
    except Exception as exc:
        answer = (
            "LLM answer generation is unavailable right now. "
            f"Reason: {exc}.\n\nTop retrieved observations are still returned below."
        )

    return QueryResponse(
        answer=answer,
        cleaned_query=cleaned_query,
        extracted_time=extracted_time,
        results=[
            _to_card(
                hit.record,
                score=round(hit.score, 4),
                vector_score=round(hit.vector_score, 4),
                keyword_score=round(hit.keyword_score, 4),
            )
            for hit in hits
        ],
    )
