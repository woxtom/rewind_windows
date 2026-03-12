from __future__ import annotations

import math
from typing import Iterable

from .database import Database
from .llm import LLMService
from .models import ObservationRecord, SearchHit
from .schemas import TimeRange


def cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def hybrid_retrieve(
    *,
    database: Database,
    llm: LLMService,
    query: str,
    extracted_time: TimeRange,
    limit: int,
) -> list[SearchHit]:
    start = extracted_time.start
    end = extracted_time.end

    cleaned_query = (query or "").strip()
    if not cleaned_query:
        recent = database.list_observations(limit=limit, start=start, end=end)
        return [
            SearchHit(record=record, score=float(limit - index), vector_score=0.0, keyword_score=0.0)
            for index, record in enumerate(recent)
        ]

    keyword_hits = database.search_keyword(cleaned_query, limit=max(limit * 3, 12), start=start, end=end)
    keyword_scores = {
        hit.id: max(0.05, 1.0 - (rank / max(1, len(keyword_hits))))
        for rank, hit in enumerate(keyword_hits)
    }

    candidates = database.list_observations(limit=500, start=start, end=end)
    vector_scores: dict[str, float] = {}
    if llm.configured and candidates:
        query_embedding = llm.embed_text(
            f"Question: {cleaned_query}\n"
            f"Time filter: {extracted_time.label or 'none'}"
        )
        for candidate in candidates:
            vector_scores[candidate.id] = max(0.0, cosine_similarity(query_embedding, candidate.embedding))

    combined: dict[str, SearchHit] = {}
    for candidate in candidates:
        v = vector_scores.get(candidate.id, 0.0)
        k = keyword_scores.get(candidate.id, 0.0)
        score = (0.65 * v) + (0.35 * k)
        if candidate.id in keyword_scores or candidate.id in vector_scores:
            combined[candidate.id] = SearchHit(
                record=candidate,
                score=score,
                vector_score=v,
                keyword_score=k,
            )

    if not combined and keyword_hits:
        for index, record in enumerate(keyword_hits[:limit]):
            combined[record.id] = SearchHit(
                record=record,
                score=max(0.05, 1.0 - (index / max(1, len(keyword_hits)))),
                vector_score=0.0,
                keyword_score=max(0.05, 1.0 - (index / max(1, len(keyword_hits)))),
            )

    if not combined:
        recent = database.list_observations(limit=limit, start=start, end=end)
        return [
            SearchHit(record=record, score=float(limit - index), vector_score=0.0, keyword_score=0.0)
            for index, record in enumerate(recent)
        ]

    ranked = sorted(combined.values(), key=lambda hit: hit.score, reverse=True)
    return ranked[:limit]
