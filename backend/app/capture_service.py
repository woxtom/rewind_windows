from __future__ import annotations

from datetime import datetime
import hashlib
import os
from pathlib import Path
from threading import Event, Lock, Thread
import traceback

from .config import Settings
from .database import Database
from .llm import LLMService


class CaptureService:
    def __init__(self, settings: Settings, database: Database, llm: LLMService):
        self.settings = settings
        self.database = database
        self.llm = llm
        self._thread: Thread | None = None
        self._stop_event = Event()
        self._state_lock = Lock()
        self._cycle_lock = Lock()
        self._state = {
            "running": False,
            "last_run_started_at": None,
            "last_run_completed_at": None,
            "last_error": None,
            "stats": {
                "windows_seen": 0,
                "observations_inserted": 0,
                "observations_extended": 0,
                "capture_failures": 0,
                "transcription_failures": 0,
                "total_observations": self.database.count_observations(),
            },
        }

    def status(self) -> dict:
        with self._state_lock:
            return {
                "running": self._state["running"],
                "last_run_started_at": self._state["last_run_started_at"],
                "last_run_completed_at": self._state["last_run_completed_at"],
                "last_error": self._state["last_error"],
                "stats": dict(self._state["stats"]),
            }

    def start(self) -> dict:
        with self._state_lock:
            if self._state["running"]:
                return self.status()
            self._state["running"] = True
            self._state["last_error"] = None
        self._stop_event.clear()
        self._thread = Thread(target=self._run_loop, name="rewind-md-capture", daemon=True)
        self._thread.start()
        return self.status()

    def stop(self) -> dict:
        self._stop_event.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=2)
        with self._state_lock:
            self._state["running"] = False
        return self.status()

    def run_once(self) -> dict:
        self._capture_cycle()
        return self.status()

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            started_monotonic = datetime.now().timestamp()
            self._capture_cycle()
            elapsed = datetime.now().timestamp() - started_monotonic
            wait_seconds = max(0.0, self.settings.capture_interval_seconds - elapsed)
            if self._stop_event.wait(wait_seconds):
                break
        with self._state_lock:
            self._state["running"] = False

    def _capture_cycle(self) -> None:
        with self._cycle_lock:
            started_at = datetime.now().astimezone()
            self._update_state(last_run_started_at=started_at, last_error=None)

            stats = {
                "windows_seen": 0,
                "observations_inserted": 0,
                "observations_extended": 0,
                "capture_failures": 0,
                "transcription_failures": 0,
                "total_observations": self.database.count_observations(),
            }

            try:
                if os.name != "nt":
                    raise RuntimeError(
                        "The capture worker uses Win32 APIs and must run on Windows. "
                        "The rest of the app can still run elsewhere."
                    )
                if not self.llm.configured:
                    raise RuntimeError("OPENAI_API_KEY is required for screenshot transcription and embeddings.")

                from .capture.windows_capture import capture_visible_windows

                cycle_dir = self.settings.screenshot_dir / started_at.strftime("%Y-%m-%d")
                captures = capture_visible_windows(cycle_dir, captured_at=started_at)
                stats["windows_seen"] = len(captures)

                for captured in captures:
                    try:
                        image_sha = _sha256_file(captured.path)
                        latest = self.database.get_latest_for_window(captured.window_key)
                        relative_path = str(captured.path.resolve().relative_to(self.settings.screenshot_dir))

                        if latest and latest.image_sha256 == image_sha:
                            self.database.extend_observation(
                                latest.id,
                                seen_at=started_at,
                                screenshot_path=latest.screenshot_path,
                                window_title=captured.title,
                                pid=captured.pid,
                            )
                            stats["observations_extended"] += 1
                            try:
                                captured.path.unlink(missing_ok=True)
                            except OSError:
                                pass
                            continue

                        transcription = self.llm.transcribe_image_to_markdown(captured.path)
                        embedding_text = _build_embedding_text(
                            window_title=captured.title,
                            seen_at=started_at,
                            markdown=transcription.markdown,
                            notes=transcription.notes,
                        )
                        embedding = self.llm.embed_text(embedding_text)
                        self.database.insert_observation(
                            window_key=captured.window_key,
                            window_title=captured.title,
                            pid=captured.pid,
                            screenshot_path=relative_path,
                            image_sha256=image_sha,
                            markdown=transcription.markdown,
                            notes=transcription.notes,
                            embedding=embedding,
                            seen_at=started_at,
                        )
                        stats["observations_inserted"] += 1
                    except Exception:
                        stats["transcription_failures"] += 1
                stats["total_observations"] = self.database.count_observations()
                self._update_state(
                    last_run_completed_at=datetime.now().astimezone(),
                    stats=stats,
                    last_error=None,
                )
            except Exception as exc:
                self._update_state(
                    last_run_completed_at=datetime.now().astimezone(),
                    stats=stats,
                    last_error=f"{exc}\n\n{traceback.format_exc(limit=3)}",
                )

    def _update_state(self, **updates) -> None:
        with self._state_lock:
            for key, value in updates.items():
                self._state[key] = value


def _sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            hasher.update(chunk)
    return hasher.hexdigest()


def _build_embedding_text(*, window_title: str, seen_at: datetime, markdown: str, notes: str) -> str:
    return (
        f"Window title: {window_title}\n"
        f"Seen at: {seen_at.isoformat()}\n\n"
        f"Markdown:\n{markdown}\n\n"
        f"Notes:\n{notes or '-'}"
    )
