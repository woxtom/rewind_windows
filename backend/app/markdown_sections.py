from __future__ import annotations

import re

_SECTION_HEADER_RE = re.compile(
    r"(?im)^[ \t]{0,3}(?:#{1,6}[ \t]*)?(MARKDOWN|NOTES)(?:[ \t]*:)?(?:[ \t]*#+)?[ \t]*$"
)


def split_markdown_sections(raw_text: str) -> tuple[str, str]:
    normalized = (raw_text or "").replace("\r\n", "\n").strip()
    if not normalized:
        return "", ""

    matches = list(_SECTION_HEADER_RE.finditer(normalized))
    markdown_headers = [match for match in matches if match.group(1).upper() == "MARKDOWN"]
    notes_headers = [match for match in matches if match.group(1).upper() == "NOTES"]

    content_start = markdown_headers[0].end() if markdown_headers else 0
    notes_header = None
    for match in notes_headers:
        if match.start() >= content_start:
            notes_header = match

    if notes_header is None:
        return normalized[content_start:].strip(), ""

    markdown = normalized[content_start:notes_header.start()].strip()
    notes = normalized[notes_header.end():].strip()
    return markdown, notes


def normalize_observation_sections(markdown: str, notes: str) -> tuple[str, str]:
    normalized_markdown = (markdown or "").replace("\r\n", "\n").strip()
    normalized_notes = (notes or "").replace("\r\n", "\n").strip()
    if normalized_notes:
        return normalized_markdown, normalized_notes

    repaired_markdown, repaired_notes = split_markdown_sections(normalized_markdown)
    if repaired_notes:
        return repaired_markdown, repaired_notes
    return normalized_markdown, normalized_notes
