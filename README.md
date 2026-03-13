# Rewind Markdown

Rewind Markdown is a local-first prototype for indexing recent screen history and querying it from a browser UI.

Today it:

1. captures the current active window on Windows at a fixed interval using `PrintWindow`,
2. transcribes each new screenshot into a `MARKDOWN` section plus a separate `NOTES` section,
3. stores screenshots on disk and observation metadata in SQLite,
4. builds both observation-level and chunk-level embeddings for retrieval, and
5. answers questions with hybrid retrieval plus natural-language time filters.

## Demo video

https://github.com/user-attachments/assets/b6b88f97-ed48-4750-af73-41ab55e3dc68

Fallback download: [rewind_windows.mp4](https://github.com/woxtom/rewind_windows/releases/download/v0.0.1/rewind_windows.mp4)

## Architecture

```text
Active window capture every N seconds (Windows only)
        |
        v
SHA-256 dedupe against the latest observation for the same window
        |
        v
Vision model -> normalized MARKDOWN / NOTES sections
        |
        v
Heading-aware chunking
        |
        v
Observation + chunk embeddings
        |
        v
SQLite (metadata, optional FTS5 indexes, embedding blobs)
        +
        +--> screenshot files on disk under data/screenshots/
        |
        v
Web UI query
        |
        +--> natural-language time extraction
        |
        +--> hybrid retrieval:
        |      chunk keyword search
        |      observation keyword search
        |      chunk vector search
        |      observation vector search
        |      time-range overlap filtering
        |
        v
LLM answer grounded on retrieved observations
```

## Main features

- Capture loop with `start`, `stop`, and `run once` controls.
- ~20GB per month
- Active-window capture built on Win32 `PrintWindow`.
- Dedupes identical screenshots by hash and also merges captures whose transcribed content did not change.
- Stores `first_seen_at`, `last_seen_at`, `capture_count`, `window_title`, `pid`, screenshot path, Markdown, and notes for each observation.
- Splits observations into heading-aware chunks for chunk-level keyword and vector retrieval.
- Uses SQLite FTS5 when available and falls back to `LIKE` matching when it is not.
- Stores embeddings in compact binary form inside SQLite
- Re-encodes screenshots to smaller `.webp` files
- Supports time filters such as:
  - `today`
  - `yesterday afternoon`
  - `last 2 hours`
  - `between 3pm and 5pm yesterday`
  - `after 9am today`
  - `before 6pm on 2026-03-10`
- Browser UI shows:
  - capture controls and status
  - parsed time filter preview
  - grounded answer output
  - retrieved observations with screenshots, Markdown, and notes
  - recent indexed observations

## Requirements

- Windows for live capture. The API and UI can still run elsewhere if you are only querying data that was already captured.
- Python 3.11 or newer.
- An `OPENAI_API_KEY` for screenshot transcription, embeddings, and final answer generation. You may select `OPENAI_API_BASE` for OpenAI-compatible endpoints if you are using a different provider or a local proxy.
- SQLite with FTS5 is recommended but not strictly required.
- Internet access for the OpenAI API.
- The default frontend also loads Tailwind, `marked`, and `DOMPurify` from public CDNs.

## Install

PowerShell:

```powershell
cd rewind_markdown_app
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

Set at least this in `.env`:

```env
OPENAI_API_KEY=your_key_here
```

## Run

```powershell
uvicorn backend.app.main:app --reload
```

Open `http://127.0.0.1:8000`.

## Environment variables

`.env.example` contains the common defaults. The full set used by the app is:

```env
OPENAI_API_KEY=
REWIND_MD_APP_NAME=Rewind Markdown
REWIND_MD_TIMEZONE=America/Los_Angeles
REWIND_MD_CAPTURE_INTERVAL_SECONDS=5
REWIND_MD_TRANSCRIBE_MODEL=gpt-4.1-mini
REWIND_MD_ANSWER_MODEL=gpt-4.1-mini
REWIND_MD_EMBEDDING_MODEL=text-embedding-3-small
REWIND_MD_CAPTURE_ENABLED_ON_STARTUP=false
REWIND_MD_MAX_QUERY_RESULTS=8
REWIND_MD_DATA_DIR=./data
REWIND_MD_SCREENSHOT_DIR=./data/screenshots
REWIND_MD_DB_PATH=./data/rewind_markdown.db
REWIND_MD_TRANSCRIBE_PROMPT=./backend/app/prompts/screenshot_to_markdown.txt
```

Notes:

- `REWIND_MD_SCREENSHOT_DIR`, `REWIND_MD_DB_PATH`, and `REWIND_MD_TRANSCRIBE_PROMPT` are optional overrides. If you omit them, the app computes sensible defaults from the repo layout.
- `REWIND_MD_CAPTURE_ENABLED_ON_STARTUP=true` starts the background capture thread when FastAPI boots.
- `/api/query` caps returned results to `min(request.limit, REWIND_MD_MAX_QUERY_RESULTS)`.

## Routes

- `GET /` serves the browser UI.
- `GET /healthz` returns a simple health payload.
- `GET /api/status` returns capture service state and per-cycle stats.
- `POST /api/capture/start` starts the background capture loop.
- `POST /api/capture/stop` stops the background capture loop.
- `POST /api/capture/run-once` runs one immediate capture and indexing cycle.
- `GET /api/observations` lists recent observations and accepts `limit`, `start`, and `end`.
- `POST /api/tools/extract-time` previews natural-language time parsing.
- `POST /api/query` retrieves matching observations and asks the answer model for a grounded response.

## Operational notes

- New screen states are the expensive path: each one requires one transcription call, one observation embedding, and a batch of chunk embeddings.
- Exact screenshot duplicates do not trigger retranscription.
- If a screenshot changes but the normalized Markdown and notes are still equivalent, the app extends the existing observation instead of inserting a new one.
- Query-time vector ranking currently loads the filtered observations and chunks into Python memory, which is fine for a prototype but not ideal for very large histories.
- If no API key is configured, new captures fail, vector retrieval is disabled, and final answer generation falls back to an explanatory message. Existing observations can still be listed, and keyword retrieval still works.

## Current limitations

- Live capture is Windows-only.
- `PrintWindow` does not work reliably for every application.
- Full-screen video, protected windows, and some GPU-heavy surfaces may capture poorly or return blank content.
- The browser UI depends on CDN-hosted frontend libraries unless you vendor them locally.
- Retrieval returns whole observations, not chunk-level snippets with per-passage citations.

## Reasonable next steps

- Multi-window capture or user-defined inclusion rules.
- Background job queues for transcription and embedding work.
- Local bundling of frontend assets for offline use.
- Retention and archival policies for screenshots and SQLite data.
- Local model fallbacks for transcription or embeddings.
- Desktop shell packaging with Electron or Tauri.

## Tests

```powershell
python -m unittest discover -s tests -v
```
