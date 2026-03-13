from __future__ import annotations

import gc
import sqlite3
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from backend.app.database import Database
from backend.app.models import ObservationChunkInput
from backend.app.retrieval import hybrid_retrieve
from backend.app.schemas import TimeRange


class FakeLLM:
    def __init__(self, embeddings: dict[str, list[float]] | None = None, *, configured: bool = True):
        self._embeddings = embeddings or {}
        self.configured = configured

    def embed_text(self, text: str) -> list[float]:
        return self._embeddings[text]


class RetrievalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db_path = Path("tests") / "test_retrieval.db"
        for suffix in ("", "-wal", "-shm"):
            candidate = Path(f"{self.db_path}{suffix}")
            if candidate.exists():
                candidate.unlink()
        self.database = Database(self.db_path)

    def tearDown(self) -> None:
        del self.database
        gc.collect()
        for suffix in ("", "-wal", "-shm"):
            candidate = Path(f"{self.db_path}{suffix}")
            if candidate.exists():
                candidate.unlink()

    def test_multilingual_keyword_queries_use_chunk_and_substring_search(self) -> None:
        now = datetime(2026, 3, 12, 22, 0, 0)
        self._insert(
            window_key="1:feishu",
            window_title="飞书",
            markdown="# 飞书\n\n团队聊天窗口",
            notes="",
            embedding=[0.1, 0.1],
            seen_at=now,
        )
        self._insert(
            window_key="1:topic",
            window_title="最新人工智能话题 - LINUX DO - Google Chrome",
            markdown="# 最新人工智能话题\n\n关于模型和硬件的讨论",
            notes="",
            embedding=[0.1, 0.1],
            seen_at=now + timedelta(minutes=1),
        )
        self._insert(
            window_key="1:mixed",
            window_title="腾讯回应OpenClaw之父Peter的抄袭指责 - Google Chrome",
            markdown="# 新闻\n\n腾讯回应OpenClaw之父Peter的抄袭指责",
            notes="",
            embedding=[0.1, 0.1],
            seen_at=now + timedelta(minutes=2),
        )

        llm = FakeLLM(configured=False)
        self.assertEqual(
            hybrid_retrieve(
                database=self.database,
                llm=llm,
                query="飞书",
                extracted_time=TimeRange(query_without_time="飞书"),
                limit=3,
            )[0].record.window_title,
            "飞书",
        )
        self.assertIn(
            "最新人工智能话题",
            hybrid_retrieve(
                database=self.database,
                llm=llm,
                query="最新人工智能话题",
                extracted_time=TimeRange(query_without_time="最新人工智能话题"),
                limit=3,
            )[0].record.window_title,
        )
        self.assertIn(
            "抄袭指责",
            hybrid_retrieve(
                database=self.database,
                llm=llm,
                query="抄袭指责",
                extracted_time=TimeRange(query_without_time="抄袭指责"),
                limit=3,
            )[0].record.window_title,
        )

    def test_keyword_ranking_prefers_exact_multi_term_match(self) -> None:
        now = datetime(2026, 3, 12, 22, 0, 0)
        self._insert(
            window_key="1:exact",
            window_title="Git repository warning fix - Google Chrome",
            markdown="# Git repository warning fix\n\nUse git config --global --add safe.directory",
            notes="",
            embedding=[0.0, 1.0],
            seen_at=now,
        )
        self._insert(
            window_key="1:partial-git",
            window_title="Git basics - Google Chrome",
            markdown="# Git tutorial\n\nInitialize a repository",
            notes="",
            embedding=[0.0, 1.0],
            seen_at=now + timedelta(minutes=1),
        )
        self._insert(
            window_key="1:partial-warning",
            window_title="Security warning - Google Chrome",
            markdown="# Browser warning\n\nCertificate problem",
            notes="",
            embedding=[0.0, 1.0],
            seen_at=now + timedelta(minutes=2),
        )

        results = hybrid_retrieve(
            database=self.database,
            llm=FakeLLM(configured=False),
            query="git repository warning fix",
            extracted_time=TimeRange(query_without_time="git repository warning fix"),
            limit=3,
        )
        self.assertEqual(results[0].record.window_title, "Git repository warning fix - Google Chrome")

    def test_chunk_vector_ranking_surfaces_dense_relevant_observation(self) -> None:
        now = datetime(2026, 3, 12, 22, 0, 0)
        self._insert(
            window_key="1:dense",
            window_title="Hacker News - Google Chrome",
            markdown="# Hacker News\n\n## Intro\nGeneral discussion\n\n## Benchmarks\nM5 Max GPU benchmarks incoming",
            notes="",
            embedding=[0.1, 0.9],
            seen_at=now,
            chunks=[
                ObservationChunkInput(
                    chunk_index=0,
                    heading_path="Intro",
                    chunk_type="markdown",
                    text="Window title: Hacker News - Google Chrome\nSection: Intro\nContent:\nGeneral discussion",
                    embedding=[0.0, 1.0],
                ),
                ObservationChunkInput(
                    chunk_index=1,
                    heading_path="Benchmarks",
                    chunk_type="markdown",
                    text="Window title: Hacker News - Google Chrome\nSection: Benchmarks\nContent:\nM5 Max GPU benchmarks incoming",
                    embedding=[1.0, 0.0],
                ),
            ],
        )
        self._insert(
            window_key="1:broad",
            window_title="M5 overview - Google Chrome",
            markdown="# M5 overview\n\nBroad high-level summary",
            notes="",
            embedding=[1.0, 0.0],
            seen_at=now + timedelta(minutes=1),
            chunks=[
                ObservationChunkInput(
                    chunk_index=0,
                    heading_path="Overview",
                    chunk_type="markdown",
                    text="Window title: M5 overview - Google Chrome\nSection: Overview\nContent:\nBroad high-level summary",
                    embedding=[0.0, 1.0],
                )
            ],
        )

        results = hybrid_retrieve(
            database=self.database,
            llm=FakeLLM({"benchmarks incoming": [1.0, 0.0]}),
            query="benchmarks incoming",
            extracted_time=TimeRange(query_without_time="benchmarks incoming"),
            limit=2,
        )
        self.assertEqual(results[0].record.window_title, "Hacker News - Google Chrome")

    def test_retrieval_considers_older_matches_beyond_previous_recent_cap(self) -> None:
        start = datetime(2026, 3, 1, 9, 0, 0)
        self._insert(
            window_key="special:old",
            window_title="Old but relevant note",
            markdown="# Notebook\n\nRare target phrase",
            notes="",
            embedding=[1.0, 0.0],
            seen_at=start,
        )
        for index in range(505):
            self._insert(
                window_key=f"recent:{index}",
                window_title=f"Recent window {index}",
                markdown="# Recent\n\nCommon content",
                notes="",
                embedding=[0.0, 1.0],
                seen_at=start + timedelta(minutes=index + 1),
            )

        results = hybrid_retrieve(
            database=self.database,
            llm=FakeLLM({"rare target": [1.0, 0.0]}),
            query="rare target",
            extracted_time=TimeRange(query_without_time="rare target"),
            limit=3,
        )
        self.assertEqual(results[0].record.window_title, "Old but relevant note")

    def test_diversity_limits_duplicate_windows_in_top_results(self) -> None:
        now = datetime(2026, 3, 12, 22, 0, 0)
        for index in range(4):
            self._insert(
                window_key="dup-window",
                window_title="Repeated page - Google Chrome",
                markdown=f"# Repeated page\n\nVariant {index}",
                notes="",
                embedding=[1.0, 0.0],
                seen_at=now + timedelta(minutes=index),
            )
        for index in range(3):
            self._insert(
                window_key=f"other:{index}",
                window_title=f"Other page {index}",
                markdown=f"# Other page\n\nAlternative result {index}",
                notes="",
                embedding=[0.9, 0.1],
                seen_at=now + timedelta(minutes=10 + index),
            )

        results = hybrid_retrieve(
            database=self.database,
            llm=FakeLLM({"repeated": [1.0, 0.0]}),
            query="repeated",
            extracted_time=TimeRange(query_without_time="repeated"),
            limit=4,
        )
        repeated = [hit for hit in results if hit.record.window_key == "dup-window"]
        self.assertEqual(len(repeated), 1)

    def test_new_rows_store_embeddings_in_blob_format(self) -> None:
        now = datetime(2026, 3, 12, 22, 0, 0)
        self._insert(
            window_key="blob:test",
            window_title="Blob format check",
            markdown="# Blob format\n\nCompact storage",
            notes="",
            embedding=[0.25, 0.5, 0.75],
            seen_at=now,
            chunks=[
                ObservationChunkInput(
                    chunk_index=0,
                    heading_path="Blob format",
                    chunk_type="markdown",
                    text="Window title: Blob format check\nSection: Blob format\nContent:\nCompact storage",
                    embedding=[0.1, 0.2, 0.3],
                )
            ],
        )

        conn = sqlite3.connect(self.db_path)
        try:
            observation_row = conn.execute(
                "SELECT embedding_json, length(embedding_blob) FROM observations LIMIT 1"
            ).fetchone()
            chunk_row = conn.execute(
                "SELECT embedding_json, length(embedding_blob) FROM observation_chunks LIMIT 1"
            ).fetchone()
        finally:
            conn.close()

        self.assertEqual(observation_row[0], "[]")
        self.assertGreater(observation_row[1], 0)
        self.assertEqual(chunk_row[0], "[]")
        self.assertGreater(chunk_row[1], 0)

    def _insert(
        self,
        *,
        window_key: str,
        window_title: str,
        markdown: str,
        notes: str,
        embedding: list[float],
        seen_at: datetime,
        chunks: list[ObservationChunkInput] | None = None,
    ) -> None:
        self.database.insert_observation(
            window_key=window_key,
            window_title=window_title,
            pid=1,
            screenshot_path="data/example.png",
            image_sha256=f"sha-{window_key}-{seen_at.isoformat()}",
            markdown=markdown,
            notes=notes,
            embedding=embedding,
            chunks=chunks,
            seen_at=seen_at,
        )


if __name__ == "__main__":
    unittest.main()
