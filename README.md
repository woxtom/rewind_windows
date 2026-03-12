# Rewind Markdown

A Rewind-like prototype that:

1. captures desktop windows every few seconds using the supplied Win32 screenshot script,
2. converts screenshots into compact Markdown using the supplied screenshot-to-Markdown prompt,
3. embeds the Markdown together with time metadata,
4. stores everything locally in SQLite, and
5. lets a web UI retrieve relevant screen history by semantic query plus time filters before sending the top results to an LLM for the final answer.

## What this uses from your upload

- `backend/app/capture/windows_capture.py` is adapted from the uploaded `window-capture.py` Win32 screenshot script.
- `backend/app/prompts/screenshot_to_markdown.txt` is copied from the uploaded screenshot-to-Markdown prompt and used directly during transcription.

## Architecture

```text
Win32 screenshots every N seconds
        |
        v
SHA-256 dedupe (skip unchanged windows)
        |
        v
Vision model -> Markdown transcription
        |
        v
Embeddings + time metadata
        |
        v
SQLite (metadata + FTS keyword index + embedding vectors)
        |
        v
Web frontend query
        |
        +--> time extraction tool (natural-language time filter)
        |
        +--> hybrid retrieval (keyword + vector + time overlap)
        |
        v
LLM answer grounded on retrieved Markdown
```

## Main features

- Capture loop with `start`, `stop`, and `run once` controls
- Uses your Win32 `PrintWindow` capture logic for visible windows
- Uses your exact screenshot-to-Markdown prompt for indexing
- Dedupe by image hash so unchanged windows only extend their time interval instead of creating a new expensive LLM call
- Stores `first_seen_at`, `last_seen_at`, and `capture_count` for each observation
- Hybrid retrieval:
  - SQLite FTS5 keyword search when available
  - embedding similarity search
  - time-range overlap filtering
- Time extraction tool for filters like:
  - `today`
  - `yesterday afternoon`
  - `last 2 hours`
  - `between 3pm and 5pm yesterday`
  - `on 2026-03-10`
- Web frontend showing:
  - capture status
  - time filter preview
  - final answer
  - retrieved observations with screenshots and Markdown
  - recent indexed observations

## Requirements

- Windows host for the capture stage (the Win32 screenshot path uses `pywin32` and `PrintWindow`)
- Python 3.11+
- OpenAI API key for screenshot transcription, embeddings, and final answer generation

The web app itself can run anywhere, but the capture worker only works on Windows.

## Install

```bash
cd rewind_markdown_app
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

Set your API key in `.env`:

```env
OPENAI_API_KEY=your_key_here
```

## Run

```bash
uvicorn backend.app.main:app --reload
```

Open:

```text
http://127.0.0.1:8000
```

## Environment variables

See `.env.example`, but the most important ones are:

```env
OPENAI_API_KEY=
REWIND_MD_TIMEZONE=America/Los_Angeles
REWIND_MD_CAPTURE_INTERVAL_SECONDS=5
REWIND_MD_TRANSCRIBE_MODEL=gpt-4.1-mini
REWIND_MD_ANSWER_MODEL=gpt-4.1-mini
REWIND_MD_EMBEDDING_MODEL=text-embedding-3-small
REWIND_MD_CAPTURE_ENABLED_ON_STARTUP=false
```

## API routes

- `GET /api/status` - capture service state
- `POST /api/capture/start` - start background capture loop
- `POST /api/capture/stop` - stop background capture loop
- `POST /api/capture/run-once` - run one immediate capture/index cycle
- `GET /api/observations` - recent indexed observations
- `POST /api/tools/extract-time` - preview time filter extraction
- `POST /api/query` - retrieve observations and ask the LLM for a grounded answer

## Notes on scaling and cost

This is a practical prototype, not a full system daemon.

Important choices in this version:

- It uses the provided screenshot prompt directly so the indexed text is diff-stable and retrieval-friendly.
- It hashes each captured image and only sends changed windows to the LLM.
- It stores one observation per stable screen state, with `first_seen_at` and `last_seen_at`, instead of blindly indexing every 5-second tick.

For a larger deployment, the next improvements would be:

- active-window-only mode
- async queue workers for transcription/embedding
- thumbnail generation and retention policies
- chunk-level indexing for very dense screens
- stronger query planning with structured outputs
- optional local embedding model fallback

## Limitations

- The capture path is Windows-only.
- Some apps do not render reliably through `PrintWindow`.
- Full-screen video, protected windows, or GPU-heavy surfaces may capture poorly.
- The first indexing pass can be expensive if many windows are open.

## Suggested next extensions

- Electron or Tauri desktop shell
- timeline scrubber UI
- background Windows service
- OCR fallback for windows that fail `PrintWindow`
- speaker/audio and clipboard indexing
- multi-device sync
