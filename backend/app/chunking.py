from __future__ import annotations

from typing import Iterable
import re

from .models import ObservationChunkInput

HEADING_RE = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")


def build_observation_chunks(
    *,
    window_title: str,
    markdown: str,
    notes: str,
    max_chars: int = 900,
) -> list[ObservationChunkInput]:
    chunks: list[ObservationChunkInput] = []
    chunk_index = 0

    for heading_path, body in _split_markdown_sections(markdown):
        for piece in _split_long_text(body, max_chars=max_chars):
            chunks.append(
                ObservationChunkInput(
                    chunk_index=chunk_index,
                    heading_path=heading_path or "Main content",
                    chunk_type="markdown",
                    text=_format_chunk_text(
                        window_title=window_title,
                        heading_path=heading_path or "Main content",
                        label="Content",
                        body=piece,
                    ),
                )
            )
            chunk_index += 1

    normalized_notes = (notes or "").replace("\r\n", "\n").strip()
    if normalized_notes:
        for piece in _split_long_text(normalized_notes, max_chars=max_chars):
            chunks.append(
                ObservationChunkInput(
                    chunk_index=chunk_index,
                    heading_path="Notes",
                    chunk_type="notes",
                    text=_format_chunk_text(
                        window_title=window_title,
                        heading_path="Notes",
                        label="Notes",
                        body=piece,
                    ),
                )
            )
            chunk_index += 1

    if chunks:
        return chunks

    return [
        ObservationChunkInput(
            chunk_index=0,
            heading_path="Main content",
            chunk_type="markdown",
            text=_format_chunk_text(
                window_title=window_title,
                heading_path="Main content",
                label="Content",
                body=(markdown or notes or "-").strip() or "-",
            ),
        )
    ]


def _split_markdown_sections(markdown: str) -> Iterable[tuple[str, str]]:
    normalized = (markdown or "").replace("\r\n", "\n").strip()
    if not normalized:
        return []

    sections: list[tuple[str, str]] = []
    heading_stack: list[str] = []
    current_heading = ""
    current_lines: list[str] = []

    for raw_line in normalized.splitlines():
        line = raw_line.strip()
        match = HEADING_RE.match(line)
        if match:
            body = "\n".join(current_lines).strip()
            if body:
                sections.append((current_heading, body))

            level = len(match.group(1))
            title = match.group(2).strip()
            heading_stack = heading_stack[: level - 1]
            heading_stack.append(title)
            current_heading = " > ".join(heading_stack)
            current_lines = []
            continue

        current_lines.append(raw_line)

    tail = "\n".join(current_lines).strip()
    if tail:
        sections.append((current_heading, tail))
    return sections


def _split_long_text(text: str, *, max_chars: int) -> list[str]:
    normalized = (text or "").strip()
    if not normalized:
        return []
    if len(normalized) <= max_chars:
        return [normalized]

    blocks = [block.strip() for block in re.split(r"\n\s*\n", normalized) if block.strip()]
    pieces: list[str] = []
    current = ""

    for block in blocks:
        candidate = f"{current}\n\n{block}".strip() if current else block
        if len(candidate) <= max_chars:
            current = candidate
            continue

        if current:
            pieces.append(current)
            current = ""

        if len(block) <= max_chars:
            current = block
            continue

        lines = [line.rstrip() for line in block.splitlines() if line.strip()]
        current_line_block = ""
        for line in lines:
            candidate_line_block = (
                f"{current_line_block}\n{line}".strip() if current_line_block else line
            )
            if len(candidate_line_block) <= max_chars:
                current_line_block = candidate_line_block
                continue

            if current_line_block:
                pieces.append(current_line_block)
            current_line_block = line

        if current_line_block:
            current = current_line_block

    if current:
        pieces.append(current)
    return pieces


def _format_chunk_text(*, window_title: str, heading_path: str, label: str, body: str) -> str:
    return (
        f"Window title: {window_title}\n"
        f"Section: {heading_path}\n"
        f"{label}:\n"
        f"{body.strip()}"
    ).strip()
