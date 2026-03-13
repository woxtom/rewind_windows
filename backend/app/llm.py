from __future__ import annotations

from dataclasses import dataclass
import base64
from pathlib import Path
from typing import Any, Sequence

try:
    from openai import OpenAI as _OpenAIClient
except Exception:  # pragma: no cover - optional until installed
    _OpenAIClient = None

from .config import Settings
from .markdown_sections import split_markdown_sections
from .models import SearchHit
from .schemas import TimeRange


@dataclass(slots=True)
class TranscriptionOutput:
    raw_text: str
    markdown: str
    notes: str


class LLMService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._client: Any | None = None
        self._transcribe_prompt = settings.transcribe_prompt_path.read_text(encoding="utf-8")

    @property
    def configured(self) -> bool:
        return bool(self.settings.openai_api_key)

    @property
    def client(self):
        if not self.settings.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY is not configured.")
        if _OpenAIClient is None:
            raise RuntimeError("The openai package is not installed. Run `pip install -r requirements.txt`.")
        if self._client is None:
            self._client = _OpenAIClient(api_key=self.settings.openai_api_key)
        return self._client

    def transcribe_image_to_markdown(self, image_path: Path) -> TranscriptionOutput:
        image_bytes = Path(image_path).read_bytes()
        base64_image = base64.b64encode(image_bytes).decode("utf-8")
        response = self.client.responses.create(
            model=self.settings.transcribe_model,
            instructions=self._transcribe_prompt,
            input=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": "Convert this screenshot into the exact two-section format from the instructions.",
                        },
                        {
                            "type": "input_image",
                            "image_url": f"data:image/png;base64,{base64_image}",
                        },
                    ],
                }
            ],
            temperature=0,
        )
        raw_text = (response.output_text or "").strip()
        markdown, notes = self._split_markdown_sections(raw_text)
        return TranscriptionOutput(raw_text=raw_text, markdown=markdown, notes=notes)

    def embed_text(self, text: str) -> list[float]:
        return self.embed_texts([text])[0]

    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        values = [text for text in texts if text is not None]
        if not values:
            return []
        response = self.client.embeddings.create(
            model=self.settings.embedding_model,
            input=values,
        )
        return [list(item.embedding) for item in response.data]

    def answer_question(
        self,
        *,
        user_query: str,
        cleaned_query: str,
        extracted_time: TimeRange,
        hits: Sequence[SearchHit],
    ) -> str:
        if not hits:
            return "No matching observations were retrieved."

        context_blocks: list[str] = []
        for index, hit in enumerate(hits, start=1):
            record = hit.record
            block = (
                f"[R{index}]\n"
                f"Window: {record.window_title}\n"
                f"PID: {record.pid}\n"
                f"First seen: {record.first_seen_at.isoformat()}\n"
                f"Last seen: {record.last_seen_at.isoformat()}\n"
                f"Capture count: {record.capture_count}\n"
                f"Markdown:\n{record.markdown}\n\n"
                f"Notes:\n{record.notes or '-'}\n"
            )
            context_blocks.append(block)

        time_label = extracted_time.label or "No explicit time filter"
        instructions = (
            "You answer questions about a user's indexed screen history. "
            "Use only the retrieved observations. "
            "Do not invent anything not supported by the context. "
            "Cite factual statements with bracketed references like [R1] or [R2]. "
            "If the context is insufficient, say so clearly. "
            "Prefer concise, grounded answers."
        )
        response = self.client.responses.create(
            model=self.settings.answer_model,
            instructions=instructions,
            input=(
                f"User question: {user_query}\n"
                f"Semantic retrieval query: {cleaned_query or user_query}\n"
                f"Applied time filter: {time_label}\n\n"
                f"Retrieved observations:\n\n" + "\n\n".join(context_blocks)
            ),
            temperature=0.2,
        )
        return (response.output_text or "").strip()

    def _split_markdown_sections(self, raw_text: str) -> tuple[str, str]:
        return split_markdown_sections(raw_text)
