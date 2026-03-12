from __future__ import annotations

from datetime import datetime
from contextlib import contextmanager
import json
from pathlib import Path
import re
import sqlite3
from typing import Iterable
import uuid

from .markdown_sections import normalize_observation_sections
from .models import ObservationRecord


class Database:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.fts_enabled = True
        self.initialize()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        return conn

    @contextmanager
    def _session(self) -> Iterable[sqlite3.Connection]:
        conn = self._connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def initialize(self) -> None:
        with self._session() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS observations (
                    id TEXT PRIMARY KEY,
                    window_key TEXT NOT NULL,
                    window_title TEXT NOT NULL,
                    pid INTEGER NOT NULL,
                    screenshot_path TEXT NOT NULL,
                    image_sha256 TEXT NOT NULL,
                    markdown TEXT NOT NULL,
                    notes TEXT NOT NULL,
                    embedding_json TEXT NOT NULL,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    capture_count INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_observations_window_key ON observations(window_key)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_observations_last_seen ON observations(last_seen_at DESC)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_observations_first_seen ON observations(first_seen_at DESC)"
            )
            try:
                conn.execute(
                    """
                    CREATE VIRTUAL TABLE IF NOT EXISTS observations_fts USING fts5(
                        observation_id UNINDEXED,
                        window_title,
                        markdown,
                        notes
                    )
                    """
                )
                self.fts_enabled = True
            except sqlite3.OperationalError:
                self.fts_enabled = False
            self._repair_observation_sections(conn)

    def _row_to_record(self, row: sqlite3.Row) -> ObservationRecord:
        markdown, notes = normalize_observation_sections(row["markdown"], row["notes"])
        return ObservationRecord(
            id=row["id"],
            window_key=row["window_key"],
            window_title=row["window_title"],
            pid=int(row["pid"]),
            screenshot_path=row["screenshot_path"],
            image_sha256=row["image_sha256"],
            markdown=markdown,
            notes=notes,
            embedding=list(json.loads(row["embedding_json"] or "[]")),
            first_seen_at=datetime.fromisoformat(row["first_seen_at"]),
            last_seen_at=datetime.fromisoformat(row["last_seen_at"]),
            capture_count=int(row["capture_count"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    def _repair_observation_sections(self, conn: sqlite3.Connection) -> None:
        rows = conn.execute(
            """
            SELECT id, window_title, markdown, notes
            FROM observations
            WHERE TRIM(notes) = ''
            """
        ).fetchall()
        for row in rows:
            markdown, notes = normalize_observation_sections(row["markdown"], row["notes"])
            if not notes or (markdown == row["markdown"] and notes == row["notes"]):
                continue

            conn.execute(
                "UPDATE observations SET markdown = ?, notes = ? WHERE id = ?",
                (markdown, notes, row["id"]),
            )
            if self.fts_enabled:
                conn.execute(
                    "DELETE FROM observations_fts WHERE observation_id = ?",
                    (row["id"],),
                )
                conn.execute(
                    """
                    INSERT INTO observations_fts (observation_id, window_title, markdown, notes)
                    VALUES (?, ?, ?, ?)
                    """,
                    (row["id"], row["window_title"], markdown, notes),
                )

    def get_latest_for_window(self, window_key: str) -> ObservationRecord | None:
        with self._session() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM observations
                WHERE window_key = ?
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                (window_key,),
            ).fetchone()
        return self._row_to_record(row) if row else None

    def insert_observation(
        self,
        *,
        window_key: str,
        window_title: str,
        pid: int,
        screenshot_path: str,
        image_sha256: str,
        markdown: str,
        notes: str,
        embedding: list[float],
        seen_at: datetime,
    ) -> ObservationRecord:
        record_id = uuid.uuid4().hex
        now_text = seen_at.isoformat()
        record = {
            "id": record_id,
            "window_key": window_key,
            "window_title": window_title,
            "pid": pid,
            "screenshot_path": screenshot_path,
            "image_sha256": image_sha256,
            "markdown": markdown,
            "notes": notes,
            "embedding_json": json.dumps(embedding),
            "first_seen_at": now_text,
            "last_seen_at": now_text,
            "capture_count": 1,
            "created_at": now_text,
            "updated_at": now_text,
        }
        with self._session() as conn:
            conn.execute(
                """
                INSERT INTO observations (
                    id, window_key, window_title, pid, screenshot_path, image_sha256,
                    markdown, notes, embedding_json, first_seen_at, last_seen_at,
                    capture_count, created_at, updated_at
                ) VALUES (
                    :id, :window_key, :window_title, :pid, :screenshot_path, :image_sha256,
                    :markdown, :notes, :embedding_json, :first_seen_at, :last_seen_at,
                    :capture_count, :created_at, :updated_at
                )
                """,
                record,
            )
            if self.fts_enabled:
                conn.execute(
                    """
                    INSERT INTO observations_fts (observation_id, window_title, markdown, notes)
                    VALUES (?, ?, ?, ?)
                    """,
                    (record_id, window_title, markdown, notes),
                )
            row = conn.execute("SELECT * FROM observations WHERE id = ?", (record_id,)).fetchone()
        return self._row_to_record(row)

    def extend_observation(
        self,
        record_id: str,
        *,
        seen_at: datetime,
        screenshot_path: str,
        window_title: str,
        pid: int,
    ) -> ObservationRecord:
        seen_text = seen_at.isoformat()
        with self._session() as conn:
            conn.execute(
                """
                UPDATE observations
                SET last_seen_at = ?,
                    capture_count = capture_count + 1,
                    screenshot_path = ?,
                    window_title = ?,
                    pid = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (seen_text, screenshot_path, window_title, pid, seen_text, record_id),
            )
            if self.fts_enabled:
                existing = conn.execute(
                    "SELECT markdown, notes FROM observations WHERE id = ?",
                    (record_id,),
                ).fetchone()
                conn.execute(
                    "DELETE FROM observations_fts WHERE observation_id = ?",
                    (record_id,),
                )
                conn.execute(
                    """
                    INSERT INTO observations_fts (observation_id, window_title, markdown, notes)
                    VALUES (?, ?, ?, ?)
                    """,
                    (record_id, window_title, existing["markdown"], existing["notes"]),
                )
            row = conn.execute("SELECT * FROM observations WHERE id = ?", (record_id,)).fetchone()
        return self._row_to_record(row)

    def list_observations(
        self,
        *,
        limit: int = 20,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[ObservationRecord]:
        start_text = start.isoformat() if start else None
        end_text = end.isoformat() if end else None
        sql = """
            SELECT *
            FROM observations
            WHERE (? IS NULL OR last_seen_at >= ?)
              AND (? IS NULL OR first_seen_at <= ?)
            ORDER BY last_seen_at DESC
            LIMIT ?
        """
        with self._session() as conn:
            rows = conn.execute(sql, (start_text, start_text, end_text, end_text, limit)).fetchall()
        return [self._row_to_record(row) for row in rows]

    def search_keyword(
        self,
        query: str,
        *,
        limit: int = 20,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[ObservationRecord]:
        normalized = self._build_keyword_query(query)
        if not normalized:
            return []

        start_text = start.isoformat() if start else None
        end_text = end.isoformat() if end else None

        with self._session() as conn:
            if self.fts_enabled:
                rows = conn.execute(
                    """
                    SELECT o.*
                    FROM observations_fts
                    JOIN observations o ON o.id = observations_fts.observation_id
                    WHERE observations_fts MATCH ?
                      AND (? IS NULL OR o.last_seen_at >= ?)
                      AND (? IS NULL OR o.first_seen_at <= ?)
                    ORDER BY bm25(observations_fts)
                    LIMIT ?
                    """,
                    (normalized, start_text, start_text, end_text, end_text, limit),
                ).fetchall()
            else:
                like_value = f"%{query.strip()}%"
                rows = conn.execute(
                    """
                    SELECT *
                    FROM observations
                    WHERE (window_title LIKE ? OR markdown LIKE ? OR notes LIKE ?)
                      AND (? IS NULL OR last_seen_at >= ?)
                      AND (? IS NULL OR first_seen_at <= ?)
                    ORDER BY last_seen_at DESC
                    LIMIT ?
                    """,
                    (
                        like_value,
                        like_value,
                        like_value,
                        start_text,
                        start_text,
                        end_text,
                        end_text,
                        limit,
                    ),
                ).fetchall()
        return [self._row_to_record(row) for row in rows]

    def count_observations(self) -> int:
        with self._session() as conn:
            row = conn.execute("SELECT COUNT(*) AS count FROM observations").fetchone()
        return int(row["count"])

    def _build_keyword_query(self, text: str) -> str:
        tokens = re.findall(r"[A-Za-z0-9_]+", text)
        if not tokens:
            return ""
        unique_tokens: list[str] = []
        for token in tokens:
            token = token.strip()
            if token and token not in unique_tokens:
                unique_tokens.append(token)
        if not unique_tokens:
            return ""
        return " OR ".join(f'"{token}"' for token in unique_tokens[:12])
