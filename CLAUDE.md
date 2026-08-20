# Rose Cinema — Project Context

AI-powered radio station generator. LLM proposes a tracklist seeded by a station's `music_source`, MusicKit verifies each track against the Apple Music catalog, DJ patter is generated and voiced via Piper, and the result is either saved as a Music Assistant playlist or pushed live to an MA player.

## Documentation discipline

**README.md must reflect current reality.** When a commit changes any of:

- a public-facing endpoint (added / removed / shape changed)
- an env var the user sets
- a runtime dependency (Docker service, external system, voice model, LLM)
- a Quick Start step
- the architecture sketch
- the "Status / what works today" picture

…update README.md in the *same* commit. The Quick Start should always actually work on a clean checkout. If you're tempted to write "(coming soon)" in the README, either build it now or open an issue and drop the line.

## Architecture decisions

- **LLM**: any OpenAI-compatible chat completions endpoint. Ollama is the default. On the server it runs as its own stack (`deploy/ollama/`) with host networking + Open WebUI on :3000; rose-cinema reaches it via `http://host.docker.internal:11434/v1`. On Mac native dev it's `http://localhost:11434/v1`. Anthropic / OpenAI / OpenRouter all work by swapping `LLM_BASE_URL` + `LLM_API_KEY` + `LLM_MODEL`. Default model is the Qwen3 MoE: `qwen3:30b-a3b-instruct-2507-q4_K_M` (30B total / 3B active, ~18 GB on disk, fits comfortably in ~19 GiB free RAM). Newer `qwen3.6:35b-a3b` is sharper but its q4_K_M needs ~25 GiB to load — won't fit on typical 16-32 GiB servers without GPU offload. Dense `qwen3.6:27b/:35b` work but are 5-10× slower on CPU.
- **Apple Music catalog**: MusicKit REST API. Developer JWT (ES256, 90-day lifetime, lazy-cached) signed with a `.p8` key. Read-only; used for track verification and canonical metadata. Private key supplied either as a file path (`MUSICKIT_PRIVATE_KEY_PATH`) or inline base64 (`MUSICKIT_PRIVATE_KEY`).
- **TTS**: Piper, via the `piper` CLI as a subprocess. Voices live under `data/piper_models/` (path is `settings.piper_data_dir`, threaded into `--data-dir`). Default fallback is `en_US-lessac-medium` (auto-downloadable). The Bryce Beattie narrator set (`cori-high`, `kristin`, `bryce`, `norman`, `mv2`, `jenny`) is baked into the Docker image.
- **Playback**: [Music Assistant](https://music-assistant.io/) is the playback bridge. It runs as its own stack (`deploy/music-assistant/docker-compose.yml`), with **host networking** on Linux so mDNS sees the LAN. radiobot talks to it over WebSocket. *No pyatv.*
- **Database**: PostgreSQL 17 via SQLAlchemy async (asyncpg driver) + Alembic. Repository pattern with abstract interfaces in `repositories/__init__.py` and SQL implementations in `repositories/sql.py`. `alembic upgrade head` runs at container start. The `DATABASE_URL` env var overrides the default connection string; Alembic auto-converts `+asyncpg` to the sync `postgresql://` scheme.
- **DJ personalities**: Markdown blob in the `djs.agent_md` column. Two samples in `agents/`: Velvet (late-night) and Spark (morning drive).

## Key abstractions

- `LLMProvider` / `TTSProvider` — abstract base classes in `providers/__init__.py`
- `MusicCatalog` (abstract) / `MusicKitCatalog` (concrete) — `services/catalog.py` + `services/musickit.py`
- `DJRepository` / `StationRepository` / `PlaylistRunRepository` — abstract in `repositories/__init__.py`
- `SeedPoolBuilder` — `services/seed_pool.py`. Resolves `music_source` to an artist (or to an artist via track-name lookup, or to genre IDs via an LLM theme map). Fans out via Apple Music's `similar-artists` and `top-songs` views (or genre charts) into a catalog-grounded candidate pool. In-memory TTL cache (24h). Falls through to legacy LLM-discovery only when nothing resolves.
- `TrackPicker` — `services/track_picker.py`. With a pool: LLM picks indices, then per-artist cap (2) + deterministic top-up. Without a pool (fallback): legacy LLM-proposes → MusicKit verifies → cap.
- `EventQueue` / `QueueWorker` — `services/queue.py`. PG LISTEN/NOTIFY queue with `FOR UPDATE SKIP LOCKED` claiming. Generation is decomposed into discrete steps (pick_tracks → generate scripts → synthesize audio → finalize → MA ingest). Each step is a `GenerationEvent` row with retry logic. Chain dispatch triggers downstream steps on completion. Crash recovery resets stale `processing` events on startup.
- `step_handlers` — `services/step_handlers.py`. One async function per step type. Thin wrappers calling existing service code (`TrackPicker`, `DJScriptService`, TTS providers, MA client).
- `DJScriptService` — `services/dj_script.py`. Verbosity scales with `babble_rate`; injects DJ personality from `agent_md`.
- `MusicAssistantClient` — `services/music_assistant.py`. One-shot WS client: `play_media` (live queue) and `save_as_playlist` (DJ MP3s ingested as `library://track/<id>` via the builtin provider, then mixed with `apple_music://track/<id>` URIs into an MA playlist).

## Station config

| Field | Range | Effect |
|---|---|---|
| `length_minutes` | 5–480 | target total runtime |
| `dj_talk_rate` | 0..1 | probability of DJ patter between tracks |
| `dj_babble_rate` | 0..1 | 0 = "that was X by Y", 1 = stories + trivia |
| `dj_max_length_secs` | 5–120 | per-segment cap |
| `weather_postal_code` | string? | US zip code for local weather injection (e.g. `02101`) |
| `weather_rate` | 0..1 | probability that a run includes weather in DJ patter |
| `music_source` | string | seed text the LLM uses to assemble the tracklist |
| `genre_variety` | 0..1 | 0 = stay in the seed's genre (strict similar-artist gate), 0.5 = historical behavior, 1 = wander freely |
| `year_variety` | 0..1 | < 0.4 applies a hard ±(5 + 62·y)-year window around the seed's median era; higher values are prompt-only ("span decades") |
| `popularity_variety` | 0..1 | 0 = top songs/hits (default, current behavior), 1 = deep cuts & B-sides; currently prompt-only |
| `excluded_artists` | string list | artists never played, even in collabs/features; cumulative with the global list in `app_settings` (`GET`/`PUT /api/settings/exclusions`) |

## What's built

- FastAPI surface with CRUD for DJs / Stations / PlaylistRuns
- `POST /api/generate` — returns immediately, enqueues `pick_tracks` as first queue step. UI polls `GET /api/runs/{id}` for progress.
- `GET /api/runs/{id}` — includes `progress` (total/completed/failed/current_step) while generating
- `GET /api/runs/{id}/events` — full event history for debugging
- `POST /api/runs/{id}/play` — push the playlist as a live queue to a Music Assistant player
- `POST /api/runs/{id}/save-to-ma` — enqueues MA chain (returns 202); auto-triggered after finalize if MA is configured
- `GET`/`PUT /api/settings/exclusions` — global excluded-artists list (app_settings table); merged with per-station `excluded_artists` at generation time
- Queue worker with PG LISTEN/NOTIFY — 10 step types, retry up to 3x, crash recovery on startup
- Web UI mounted at `/` — list stations, "Generate" button (polls for progress)
- Alembic migrations 001–016
- Docker stack: PostgreSQL 17 + radiobot, Portainer-managed

## Notable backlog (GitHub issues)

#1 station variety sliders · #2 Music-Map · #3 MusicBrainz · #4 iOS Shortcut · #5 today-in-history chitchat · #6 nightly news scrape · #7 full create/edit web UI · #10 length-budget top-up · #16 no-repeat memory · #18 Jellyfin/Lidarr · #19 mixed-source playback · #21 weather forecast in DJ chitchat

## Commands

Server (Portainer-managed):

```bash
# After stack is up, one-time model pull:
docker compose exec ollama ollama pull qwen3:30b-a3b-instruct-2507-q4_K_M
```

Local dev (Apple Silicon):

```bash
brew services start ollama
ollama pull qwen3:30b-a3b-instruct-2507-q4_K_M
.venv/bin/alembic upgrade head
.venv/bin/uvicorn rose_cinema.api:app --host 0.0.0.0 --port 8765
.venv/bin/pytest tests/
```

## Code style

- Python 3.12, `from __future__ import annotations`, type hints everywhere
- Async throughout (SQLAlchemy async, FastAPI async, httpx async, websockets)
- Pydantic for API schemas, pydantic-settings for config
- No ORM leakage past the repository boundary — DTOs only
- No comments unless the *why* is non-obvious
