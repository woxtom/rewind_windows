from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
import os

BASE_DIR = Path(__file__).resolve().parents[2]
APP_DIR = Path(__file__).resolve().parent


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    app_name: str
    timezone: str
    capture_interval_seconds: int
    data_dir: Path
    screenshot_dir: Path
    db_path: Path
    transcribe_model: str
    answer_model: str
    embedding_model: str
    openai_api_key: str | None
    transcribe_prompt_path: Path
    max_query_results: int
    capture_enabled_on_startup: bool


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    data_dir = Path(os.getenv("REWIND_MD_DATA_DIR", str(BASE_DIR / "data"))).resolve()
    screenshot_dir = Path(
        os.getenv("REWIND_MD_SCREENSHOT_DIR", str(data_dir / "screenshots"))
    ).resolve()
    db_path = Path(os.getenv("REWIND_MD_DB_PATH", str(data_dir / "rewind_markdown.db"))).resolve()
    transcribe_prompt_path = Path(
        os.getenv(
            "REWIND_MD_TRANSCRIBE_PROMPT",
            str(APP_DIR / "prompts" / "screenshot_to_markdown.txt"),
        )
    ).resolve()

    data_dir.mkdir(parents=True, exist_ok=True)
    screenshot_dir.mkdir(parents=True, exist_ok=True)

    return Settings(
        app_name=os.getenv("REWIND_MD_APP_NAME", "Rewind Markdown"),
        timezone=os.getenv("REWIND_MD_TIMEZONE", "America/Los_Angeles"),
        capture_interval_seconds=max(1, int(os.getenv("REWIND_MD_CAPTURE_INTERVAL_SECONDS", "5"))),
        data_dir=data_dir,
        screenshot_dir=screenshot_dir,
        db_path=db_path,
        transcribe_model=os.getenv("REWIND_MD_TRANSCRIBE_MODEL", "gpt-4.1-mini"),
        answer_model=os.getenv("REWIND_MD_ANSWER_MODEL", "gpt-4.1-mini"),
        embedding_model=os.getenv("REWIND_MD_EMBEDDING_MODEL", "text-embedding-3-small"),
        openai_api_key=os.getenv("OPENAI_API_KEY"),
        transcribe_prompt_path=transcribe_prompt_path,
        max_query_results=max(1, int(os.getenv("REWIND_MD_MAX_QUERY_RESULTS", "8"))),
        capture_enabled_on_startup=_env_bool("REWIND_MD_CAPTURE_ENABLED_ON_STARTUP", False),
    )
