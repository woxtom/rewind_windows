from __future__ import annotations

import gc
import json
import sqlite3
import unittest
from datetime import datetime
from pathlib import Path

from backend.app.database import Database
from backend.app.markdown_sections import normalize_observation_sections, split_markdown_sections


class SplitMarkdownSectionsTests(unittest.TestCase):
    def test_splits_plain_section_labels(self) -> None:
        markdown, notes = split_markdown_sections(
            "MARKDOWN\n# App\n\n## Body\n- item\n\nNOTES\n- uncertain"
        )
        self.assertEqual(markdown, "# App\n\n## Body\n- item")
        self.assertEqual(notes, "- uncertain")

    def test_splits_heading_style_notes_section(self) -> None:
        markdown, notes = split_markdown_sections(
            "# App\n\n## Body\n- item\n\n## NOTES\n- uncertain"
        )
        self.assertEqual(markdown, "# App\n\n## Body\n- item")
        self.assertEqual(notes, "- uncertain")

    def test_uses_last_notes_header(self) -> None:
        markdown, notes = split_markdown_sections(
            "# App\n\n## NOTES\n- visible section title\n\n## Footer\n- item\n\n## NOTES\n- uncertainty"
        )
        self.assertEqual(markdown, "# App\n\n## NOTES\n- visible section title\n\n## Footer\n- item")
        self.assertEqual(notes, "- uncertainty")

    def test_normalize_preserves_existing_notes(self) -> None:
        markdown, notes = normalize_observation_sections("# App\n\n## NOTES\n- content", "- stored note")
        self.assertEqual(markdown, "# App\n\n## NOTES\n- content")
        self.assertEqual(notes, "- stored note")


class DatabaseNormalizationTests(unittest.TestCase):
    def test_initialize_repairs_existing_rows_with_embedded_notes(self) -> None:
        db_path = Path("tests") / "test_rewind_markdown.db"
        for suffix in ("", "-wal", "-shm"):
            candidate = Path(f"{db_path}{suffix}")
            if candidate.exists():
                candidate.unlink()
        try:
            conn = sqlite3.connect(db_path)
            conn.execute(
                """
                CREATE TABLE observations (
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
                """
                CREATE VIRTUAL TABLE observations_fts USING fts5(
                    observation_id UNINDEXED,
                    window_title,
                    markdown,
                    notes
                )
                """
            )
            now = datetime(2026, 3, 12, 17, 0, 0).isoformat()
            broken_markdown = "# Windows PowerShell\n\n## Command history\n```powershell\ncode .\n```\n\n## NOTES\n- highlighted command"
            conn.execute(
                """
                INSERT INTO observations (
                    id, window_key, window_title, pid, screenshot_path, image_sha256,
                    markdown, notes, embedding_json, first_seen_at, last_seen_at,
                    capture_count, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "obs-1",
                    "window-1",
                    "Windows PowerShell",
                    123,
                    "data/example.png",
                    "sha256",
                    broken_markdown,
                    "",
                    json.dumps([0.1, 0.2]),
                    now,
                    now,
                    1,
                    now,
                    now,
                ),
            )
            conn.execute(
                """
                INSERT INTO observations_fts (observation_id, window_title, markdown, notes)
                VALUES (?, ?, ?, ?)
                """,
                ("obs-1", "Windows PowerShell", broken_markdown, ""),
            )
            conn.commit()
            conn.close()

            database = Database(db_path)
            record = database.list_observations(limit=1)[0]

            self.assertEqual(
                record.markdown,
                "# Windows PowerShell\n\n## Command history\n```powershell\ncode .\n```",
            )
            self.assertEqual(record.notes, "- highlighted command")
            del database
            gc.collect()
        finally:
            for suffix in ("", "-wal", "-shm"):
                candidate = Path(f"{db_path}{suffix}")
                if candidate.exists():
                    try:
                        candidate.unlink()
                    except PermissionError:
                        pass


if __name__ == "__main__":
    unittest.main()
