from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
import json
from pathlib import Path
import re
import sqlite3
import struct
from typing import Iterable
import uuid

from .chunking import build_observation_chunks
from .markdown_sections import normalize_observation_sections
from .models import ObservationChunkInput, ObservationChunkRecord, ObservationRecord

EMPTY_EMBEDDING_JSON = "[]"


def _serialize_embedding(values: list[float]) -> bytes:
    if not values:
        return b""
    return struct.pack(f"<{len(values)}f", *values)


def _deserialize_embedding(blob_value: bytes | None, json_value: str | None) -> list[float]:
    if blob_value:
        raw = bytes(blob_value)
        if len(raw) % 4 != 0:
            raise ValueError("Embedding blob length must be divisible by 4 bytes.")
        count = len(raw) // 4
        return list(struct.unpack(f"<{count}f", raw))
    return list(json.loads(json_value or EMPTY_EMBEDDING_JSON))


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

    def _ensure_column(
        self,
        conn: sqlite3.Connection,
        table_name: str,
        column_name: str,
        column_def: str,
    ) -> None:
        rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        existing = {row["name"] for row in rows}
        if column_name in existing:
            return
        conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_def}")

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
                    embedding_json TEXT NOT NULL DEFAULT '[]',
                    embedding_blob BLOB,
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
                conn.execute(
                    """
                    CREATE VIRTUAL TABLE IF NOT EXISTS observation_chunks_fts USING fts5(
                        chunk_id UNINDEXED,
                        observation_id UNINDEXED,
                        window_title,
                        heading_path,
                        text
                    )
                    """
                )
                self.fts_enabled = True
            except sqlite3.OperationalError:
                self.fts_enabled = False

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS observation_chunks (
                    id TEXT PRIMARY KEY,
                    observation_id TEXT NOT NULL,
                    chunk_index INTEGER NOT NULL,
                    heading_path TEXT NOT NULL,
                    chunk_type TEXT NOT NULL,
                    text TEXT NOT NULL,
                    embedding_json TEXT NOT NULL DEFAULT '[]',
                    embedding_blob BLOB,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(observation_id, chunk_index)
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_observation_chunks_observation_id ON observation_chunks(observation_id)"
            )
            self._ensure_column(conn, "observations", "embedding_blob", "BLOB")
            self._ensure_column(conn, "observation_chunks", "embedding_blob", "BLOB")
            self._repair_observation_sections(conn)
            self._backfill_observation_chunks(conn)
            self._compact_embedding_storage(conn)

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
            embedding=_deserialize_embedding(row["embedding_blob"], row["embedding_json"]),
            first_seen_at=datetime.fromisoformat(row["first_seen_at"]),
            last_seen_at=datetime.fromisoformat(row["last_seen_at"]),
            capture_count=int(row["capture_count"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    def _row_to_chunk_record(self, row: sqlite3.Row) -> ObservationChunkRecord:
        return ObservationChunkRecord(
            id=row["id"],
            observation_id=row["observation_id"],
            window_title=row["window_title"],
            chunk_index=int(row["chunk_index"]),
            heading_path=row["heading_path"],
            chunk_type=row["chunk_type"],
            text=row["text"],
            embedding=_deserialize_embedding(row["embedding_blob"], row["embedding_json"]),
            first_seen_at=datetime.fromisoformat(row["first_seen_at"]),
            last_seen_at=datetime.fromisoformat(row["last_seen_at"]),
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

    def _backfill_observation_chunks(self, conn: sqlite3.Connection) -> None:
        rows = conn.execute(
            """
            SELECT o.id, o.window_title, o.markdown, o.notes, o.created_at
            FROM observations o
            WHERE NOT EXISTS (
                SELECT 1
                FROM observation_chunks c
                WHERE c.observation_id = o.id
            )
            """
        ).fetchall()
        for row in rows:
            markdown, notes = normalize_observation_sections(row["markdown"], row["notes"])
            chunks = build_observation_chunks(
                window_title=row["window_title"],
                markdown=markdown,
                notes=notes,
            )
            self._replace_observation_chunks(
                conn,
                observation_id=row["id"],
                window_title=row["window_title"],
                chunks=chunks,
                now_text=row["created_at"],
            )

    def _replace_observation_chunks(
        self,
        conn: sqlite3.Connection,
        *,
        observation_id: str,
        window_title: str,
        chunks: list[ObservationChunkInput],
        now_text: str,
    ) -> None:
        conn.execute("DELETE FROM observation_chunks WHERE observation_id = ?", (observation_id,))
        if self.fts_enabled:
            conn.execute("DELETE FROM observation_chunks_fts WHERE observation_id = ?", (observation_id,))

        for chunk in chunks:
            chunk_id = uuid.uuid4().hex
            conn.execute(
                """
                INSERT INTO observation_chunks (
                    id, observation_id, chunk_index, heading_path, chunk_type, text,
                    embedding_json, embedding_blob, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    chunk_id,
                    observation_id,
                    chunk.chunk_index,
                    chunk.heading_path,
                    chunk.chunk_type,
                    chunk.text,
                    EMPTY_EMBEDDING_JSON,
                    _serialize_embedding(chunk.embedding or []),
                    now_text,
                    now_text,
                ),
            )
            if self.fts_enabled:
                conn.execute(
                    """
                    INSERT INTO observation_chunks_fts (
                        chunk_id, observation_id, window_title, heading_path, text
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (chunk_id, observation_id, window_title, chunk.heading_path, chunk.text),
                )

    def _refresh_observation_chunk_titles(
        self,
        conn: sqlite3.Connection,
        *,
        observation_id: str,
        window_title: str,
    ) -> None:
        if not self.fts_enabled:
            return
        rows = conn.execute(
            """
            SELECT id, observation_id, heading_path, text
            FROM observation_chunks
            WHERE observation_id = ?
            ORDER BY chunk_index ASC
            """,
            (observation_id,),
        ).fetchall()
        conn.execute("DELETE FROM observation_chunks_fts WHERE observation_id = ?", (observation_id,))
        for row in rows:
            conn.execute(
                """
                INSERT INTO observation_chunks_fts (
                    chunk_id, observation_id, window_title, heading_path, text
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    row["id"],
                    row["observation_id"],
                    window_title,
                    row["heading_path"],
                    row["text"],
                ),
            )

    def _compact_embedding_storage(self, conn: sqlite3.Connection) -> None:
        self._compact_table_embeddings(
            conn,
            table_name="observations",
            id_column="id",
        )
        self._compact_table_embeddings(
            conn,
            table_name="observation_chunks",
            id_column="id",
        )

    def _compact_table_embeddings(
        self,
        conn: sqlite3.Connection,
        *,
        table_name: str,
        id_column: str,
    ) -> None:
        rows = conn.execute(
            f"""
            SELECT {id_column} AS record_id, embedding_json, embedding_blob
            FROM {table_name}
            WHERE COALESCE(embedding_json, '') != ?
               OR embedding_blob IS NULL
            """,
            (EMPTY_EMBEDDING_JSON,),
        ).fetchall()
        for row in rows:
            try:
                embedding = _deserialize_embedding(row["embedding_blob"], row["embedding_json"])
            except (ValueError, json.JSONDecodeError, struct.error, TypeError):
                continue
            conn.execute(
                f"""
                UPDATE {table_name}
                SET embedding_json = ?, embedding_blob = ?
                WHERE {id_column} = ?
                """,
                (
                    EMPTY_EMBEDDING_JSON,
                    _serialize_embedding(embedding),
                    row["record_id"],
                ),
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
        chunks: list[ObservationChunkInput] | None = None,
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
            "embedding_json": EMPTY_EMBEDDING_JSON,
            "embedding_blob": _serialize_embedding(embedding),
            "first_seen_at": now_text,
            "last_seen_at": now_text,
            "capture_count": 1,
            "created_at": now_text,
            "updated_at": now_text,
        }
        chunk_payloads = chunks or build_observation_chunks(
            window_title=window_title,
            markdown=markdown,
            notes=notes,
        )
        with self._session() as conn:
            conn.execute(
                """
                INSERT INTO observations (
                    id, window_key, window_title, pid, screenshot_path, image_sha256,
                    markdown, notes, embedding_json, embedding_blob, first_seen_at, last_seen_at,
                    capture_count, created_at, updated_at
                ) VALUES (
                    :id, :window_key, :window_title, :pid, :screenshot_path, :image_sha256,
                    :markdown, :notes, :embedding_json, :embedding_blob, :first_seen_at, :last_seen_at,
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
            self._replace_observation_chunks(
                conn,
                observation_id=record_id,
                window_title=window_title,
                chunks=chunk_payloads,
                now_text=now_text,
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
            self._refresh_observation_chunk_titles(
                conn,
                observation_id=record_id,
                window_title=window_title,
            )
            row = conn.execute("SELECT * FROM observations WHERE id = ?", (record_id,)).fetchone()
        return self._row_to_record(row)

    def list_observations(
        self,
        *,
        limit: int | None = 20,
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
        """
        params: list[object] = [start_text, start_text, end_text, end_text]
        if limit is not None:
            sql += "\n            LIMIT ?"
            params.append(limit)
        with self._session() as conn:
            rows = conn.execute(sql, tuple(params)).fetchall()
        return [self._row_to_record(row) for row in rows]

    def list_observation_chunks(
        self,
        *,
        limit: int | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[ObservationChunkRecord]:
        start_text = start.isoformat() if start else None
        end_text = end.isoformat() if end else None
        sql = """
            SELECT c.*, o.window_title, o.first_seen_at, o.last_seen_at
            FROM observation_chunks c
            JOIN observations o ON o.id = c.observation_id
            WHERE (? IS NULL OR o.last_seen_at >= ?)
              AND (? IS NULL OR o.first_seen_at <= ?)
            ORDER BY o.last_seen_at DESC, c.chunk_index ASC
        """
        params: list[object] = [start_text, start_text, end_text, end_text]
        if limit is not None:
            sql += "\n            LIMIT ?"
            params.append(limit)
        with self._session() as conn:
            rows = conn.execute(sql, tuple(params)).fetchall()
        return [self._row_to_chunk_record(row) for row in rows]

    def search_keyword(
        self,
        query: str,
        *,
        limit: int = 20,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[ObservationRecord]:
        return self.search_keyword_observations(query, limit=limit, start=start, end=end)

    def search_keyword_observations(
        self,
        query: str,
        *,
        limit: int = 20,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[ObservationRecord]:
        terms = self._extract_keyword_terms(query)
        fts_queries = self._build_keyword_queries(query)
        if not terms and not fts_queries:
            return []

        start_text = start.isoformat() if start else None
        end_text = end.isoformat() if end else None
        ordered_rows: list[sqlite3.Row] = []
        seen_ids: set[str] = set()

        with self._session() as conn:
            if self.fts_enabled:
                for match_query in fts_queries:
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
                        (match_query, start_text, start_text, end_text, end_text, limit),
                    ).fetchall()
                    for row in rows:
                        if row["id"] in seen_ids:
                            continue
                        ordered_rows.append(row)
                        seen_ids.add(row["id"])

            if terms:
                like_rows = conn.execute(
                    self._observation_like_sql(terms),
                    self._observation_like_params(
                        terms,
                        start_text=start_text,
                        end_text=end_text,
                        limit=limit,
                    ),
                ).fetchall()
                for row in like_rows:
                    if row["id"] in seen_ids:
                        continue
                    ordered_rows.append(row)
                    seen_ids.add(row["id"])

        return [self._row_to_record(row) for row in ordered_rows[:limit]]

    def search_keyword_chunks(
        self,
        query: str,
        *,
        limit: int = 20,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[ObservationRecord]:
        terms = self._extract_keyword_terms(query)
        fts_queries = self._build_keyword_queries(query)
        if not terms and not fts_queries:
            return []

        start_text = start.isoformat() if start else None
        end_text = end.isoformat() if end else None
        ordered_rows: list[sqlite3.Row] = []
        seen_ids: set[str] = set()

        with self._session() as conn:
            if self.fts_enabled:
                for match_query in fts_queries:
                    rows = conn.execute(
                        """
                        SELECT o.*
                        FROM observation_chunks_fts
                        JOIN observations o ON o.id = observation_chunks_fts.observation_id
                        WHERE observation_chunks_fts MATCH ?
                          AND (? IS NULL OR o.last_seen_at >= ?)
                          AND (? IS NULL OR o.first_seen_at <= ?)
                        ORDER BY bm25(observation_chunks_fts)
                        """,
                        (match_query, start_text, start_text, end_text, end_text),
                    ).fetchall()
                    for row in rows:
                        if row["id"] in seen_ids:
                            continue
                        ordered_rows.append(row)
                        seen_ids.add(row["id"])
                        if len(ordered_rows) >= limit:
                            break
                    if len(ordered_rows) >= limit:
                        break

            if terms:
                like_rows = conn.execute(
                    self._chunk_like_sql(terms),
                    self._chunk_like_params(
                        terms,
                        start_text=start_text,
                        end_text=end_text,
                        limit=limit,
                    ),
                ).fetchall()
                for row in like_rows:
                    if row["id"] in seen_ids:
                        continue
                    ordered_rows.append(row)
                    seen_ids.add(row["id"])

        return [self._row_to_record(row) for row in ordered_rows[:limit]]

    def count_observations(self) -> int:
        with self._session() as conn:
            row = conn.execute("SELECT COUNT(*) AS count FROM observations").fetchone()
        return int(row["count"])

    def _extract_keyword_terms(self, text: str) -> list[str]:
        pattern = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.:/-]*|[\u3400-\u9fff]+")
        terms: list[str] = []
        for token in pattern.findall(text or ""):
            value = token.strip()
            if not value:
                continue
            if value.isascii() and len(value) < 2:
                continue
            if value not in terms:
                terms.append(value)
        return terms[:12]

    def _build_keyword_queries(self, text: str) -> list[str]:
        terms = self._extract_keyword_terms(text)
        if not terms:
            return []

        queries: list[str] = []
        strict_terms = terms[:4]
        if len(strict_terms) == 1:
            queries.append(f'"{strict_terms[0]}"')
        else:
            queries.append(" AND ".join(f'"{term}"' for term in strict_terms))
        if len(terms) > 1:
            queries.append(" OR ".join(f'"{term}"' for term in terms[:8]))
        return list(dict.fromkeys(queries))

    def _observation_like_sql(self, terms: list[str]) -> str:
        clauses = [
            "(window_title LIKE ? OR markdown LIKE ? OR notes LIKE ?)"
            for _ in terms
        ]
        return f"""
            SELECT *
            FROM observations
            WHERE ({' OR '.join(clauses)})
              AND (? IS NULL OR last_seen_at >= ?)
              AND (? IS NULL OR first_seen_at <= ?)
            ORDER BY last_seen_at DESC
            LIMIT ?
        """

    def _observation_like_params(
        self,
        terms: list[str],
        *,
        start_text: str | None,
        end_text: str | None,
        limit: int,
    ) -> tuple[object, ...]:
        params: list[object] = []
        for term in terms:
            like_value = f"%{term}%"
            params.extend([like_value, like_value, like_value])
        params.extend([start_text, start_text, end_text, end_text, limit])
        return tuple(params)

    def _chunk_like_sql(self, terms: list[str]) -> str:
        clauses = [
            "(c.text LIKE ? OR o2.window_title LIKE ?)"
            for _ in terms
        ]
        return f"""
            SELECT o.*
            FROM observations o
            JOIN (
                SELECT c.observation_id, MAX(o2.last_seen_at) AS last_seen
                FROM observation_chunks c
                JOIN observations o2 ON o2.id = c.observation_id
                WHERE ({' OR '.join(clauses)})
                  AND (? IS NULL OR o2.last_seen_at >= ?)
                  AND (? IS NULL OR o2.first_seen_at <= ?)
                GROUP BY c.observation_id
                ORDER BY last_seen DESC
                LIMIT ?
            ) ranked ON ranked.observation_id = o.id
            ORDER BY ranked.last_seen DESC
        """

    def _chunk_like_params(
        self,
        terms: list[str],
        *,
        start_text: str | None,
        end_text: str | None,
        limit: int,
    ) -> tuple[object, ...]:
        params: list[object] = []
        for term in terms:
            like_value = f"%{term}%"
            params.extend([like_value, like_value])
        params.extend([start_text, start_text, end_text, end_text, limit])
        return tuple(params)

    def _build_keyword_query(self, text: str) -> str:
        queries = self._build_keyword_queries(text)
        return queries[0] if queries else ""
