from __future__ import annotations

import math

from .database import Database
from .llm import LLMService
from .models import ObservationChunkRecord, ObservationRecord, SearchHit
from .schemas import TimeRange

RRF_K = 60
MAX_RESULTS_PER_WINDOW = 1


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
    all_observations = database.list_observations(limit=None, start=start, end=end)
    observation_by_id = {record.id: record for record in all_observations}

    cleaned_query = (query or "").strip()
    if not cleaned_query:
        return _recent_hits(all_observations, limit=limit)

    candidate_limit = max(limit * 8, 40)
    keyword_lists = [
        database.search_keyword_chunks(cleaned_query, limit=candidate_limit, start=start, end=end),
        database.search_keyword_observations(cleaned_query, limit=candidate_limit, start=start, end=end),
    ]

    keyword_scores: dict[str, float] = {}
    vector_scores: dict[str, float] = {}

    for ranked_list in keyword_lists:
        _add_rrf_scores(keyword_scores, ranked_list)

    if llm.configured and all_observations:
        query_embedding = llm.embed_text(cleaned_query)
        _add_rrf_scores(
            vector_scores,
            _rank_observation_vectors(query_embedding, all_observations, limit=candidate_limit),
        )
        chunk_records = database.list_observation_chunks(limit=None, start=start, end=end)
        _add_rrf_scores(
            vector_scores,
            _rank_chunk_vectors(
                query_embedding,
                chunk_records,
                observation_by_id,
                limit=candidate_limit,
            ),
        )

    combined_ids = set(keyword_scores) | set(vector_scores)
    if not combined_ids:
        return _recent_hits(all_observations, limit=limit)

    hits = [
        SearchHit(
            record=observation_by_id[record_id],
            score=keyword_scores.get(record_id, 0.0)
            + vector_scores.get(record_id, 0.0)
            + _title_match_boost(observation_by_id[record_id], cleaned_query),
            vector_score=vector_scores.get(record_id, 0.0),
            keyword_score=keyword_scores.get(record_id, 0.0),
        )
        for record_id in combined_ids
        if record_id in observation_by_id
    ]
    hits.sort(key=lambda hit: (hit.score, hit.record.last_seen_at), reverse=True)
    return _diversify_hits(hits, limit=limit)


def _recent_hits(records: list[ObservationRecord], *, limit: int) -> list[SearchHit]:
    seed = records[: max(limit * 3, limit)]
    hits = [
        SearchHit(record=record, score=float(limit - index), vector_score=0.0, keyword_score=0.0)
        for index, record in enumerate(seed)
    ]
    return _diversify_hits(hits, limit=limit)


def _rank_observation_vectors(
    query_embedding: list[float],
    observations: list[ObservationRecord],
    *,
    limit: int,
) -> list[ObservationRecord]:
    scored = [
        (max(0.0, cosine_similarity(query_embedding, candidate.embedding)), candidate)
        for candidate in observations
    ]
    ranked = [candidate for score, candidate in sorted(scored, key=lambda item: item[0], reverse=True) if score > 0]
    return ranked[:limit]


def _rank_chunk_vectors(
    query_embedding: list[float],
    chunks: list[ObservationChunkRecord],
    observation_by_id: dict[str, ObservationRecord],
    *,
    limit: int,
) -> list[ObservationRecord]:
    chunk_scores: dict[str, list[float]] = {}
    for chunk in chunks:
        if not chunk.embedding:
            continue
        similarity = max(0.0, cosine_similarity(query_embedding, chunk.embedding))
        if similarity <= 0:
            continue
        chunk_scores.setdefault(chunk.observation_id, []).append(similarity)

    aggregated: list[tuple[float, ObservationRecord]] = []
    for observation_id, scores in chunk_scores.items():
        record = observation_by_id.get(observation_id)
        if not record:
            continue
        top_scores = sorted(scores, reverse=True)[:2]
        aggregate = top_scores[0] + (0.15 * sum(top_scores[1:]))
        aggregated.append((aggregate, record))

    aggregated.sort(key=lambda item: item[0], reverse=True)
    return [record for _, record in aggregated[:limit]]


def _add_rrf_scores(target: dict[str, float], ranked_list: list[ObservationRecord]) -> None:
    seen_ids: set[str] = set()
    for rank, record in enumerate(ranked_list, start=1):
        if record.id in seen_ids:
            continue
        target[record.id] = target.get(record.id, 0.0) + (1.0 / (RRF_K + rank))
        seen_ids.add(record.id)


def _diversify_hits(hits: list[SearchHit], *, limit: int) -> list[SearchHit]:
    selected: list[SearchHit] = []
    deferred: list[SearchHit] = []
    counts: dict[str, int] = {}

    for hit in hits:
        diversity_key = _diversity_key(hit.record)
        window_count = counts.get(diversity_key, 0)
        if window_count < MAX_RESULTS_PER_WINDOW:
            selected.append(hit)
            counts[diversity_key] = window_count + 1
            if len(selected) >= limit:
                return selected
            continue
        deferred.append(hit)

    for hit in deferred:
        selected.append(hit)
        if len(selected) >= limit:
            break
    return selected


def _title_match_boost(record: ObservationRecord, query: str) -> float:
    normalized_query = (query or "").strip().casefold()
    normalized_title = (record.window_title or "").strip().casefold()
    if not normalized_query or not normalized_title:
        return 0.0
    if normalized_title == normalized_query:
        return 0.02
    if normalized_query in normalized_title:
        return 0.005
    return 0.0


def _diversity_key(record: ObservationRecord) -> str:
    title = (record.window_title or "").strip().casefold()
    return title or record.window_key
