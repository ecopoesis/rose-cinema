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

- **LLM**: any OpenAI-compatible chat completions endpoint. Ollama is the default. On the server it runs as its own stack (`deploy/ollama/`) with host networking + Open WebUI on :3000; rose-cinema reaches it via `http://host.docker.internal:11434/v1`. On Mac native dev it's `http://localhost:11434/v1`. Anthropic / OpenAI / OpenRouter all work by swapping `LLM_BASE_URL` + `LLM_API_KEY` + `LLM_MODEL`. Default model is the MoE variant: `hf.co/bartowski/Qwen_Qwen3.6-35B-A3B-GGUF:Q4_K_M` (35B total / 3B active, ~21 GB on disk, fast on CPU because only 3B params activate per token). Pulled from HF via Ollama because the official `qwen3.6` Ollama namespace doesn't ship the A3B variant. The dense `qwen3.6:27b` and `:35b` exist but are much slower on CPU; only worth using if you have GPU offload.
- **Apple Music catalog**: MusicKit REST API. Developer JWT (ES256, 90-day lifetime, lazy-cached) signed with a `.p8` key. Read-only; used for track verification and canonical metadata. Private key supplied either as a file path (`MUSICKIT_PRIVATE_KEY_PATH`) or inline base64 (`MUSICKIT_PRIVATE_KEY`).
- **TTS**: Piper, via the `piper` CLI as a subprocess. Voices live under `data/piper_models/` (path is `settings.piper_data_dir`, threaded into `--data-dir`). Default fallback is `en_US-lessac-medium` (auto-downloadable). The Bryce Beattie narrator set (`cori-high`, `kristin`, `bryce`, `norman`, `mv2`, `jenny`) is baked into the Docker image.
- **Playback**: [Music Assistant](https://music-assistant.io/) is the playback bridge. It runs as its own stack (`deploy/music-assistant/docker-compose.yml`), with **host networking** on Linux so mDNS sees the LAN. radiobot talks to it over WebSocket. *No pyatv.*
- **Database**: SQLite via SQLAlchemy async + Alembic. Repository pattern with abstract interfaces in `repositories/__init__.py` and SQLite implementations in `repositories/sqlite.py`. `alembic upgrade head` runs at container start.
- **DJ personalities**: Markdown blob in the `djs.agent_md` column. Two samples in `agents/`: Velvet (late-night) and Spark (morning drive).

## Key abstractions

- `LLMProvider` / `TTSProvider` — abstract base classes in `providers/__init__.py`
- `MusicCatalog` (abstract) / `MusicKitCatalog` (concrete) — `services/catalog.py` + `services/musickit.py`
- `DJRepository` / `StationRepository` / `PlaylistRunRepository` — abstract in `repositories/__init__.py`
- `SeedPoolBuilder` — `services/seed_pool.py`. Resolves `music_source` to an artist (or to an artist via track-name lookup, or to genre IDs via an LLM theme map). Fans out via Apple Music's `similar-artists` and `top-songs` views (or genre charts) into a catalog-grounded candidate pool. In-memory TTL cache (24h). Falls through to legacy LLM-discovery only when nothing resolves.
- `TrackPicker` — `services/track_picker.py`. With a pool: LLM picks indices, then per-artist cap (2) + deterministic top-up. Without a pool (fallback): legacy LLM-proposes → MusicKit verifies → cap.
- `StationBuilder` — `services/station_builder.py`. Takes the verified tracklist, decides DJ-segment placement (`should_talk` rolls dice on `talk_rate`), generates scripts, synthesizes audio, returns the `PlaylistEntry` list.
- `DJScriptService` — `services/dj_script.py`. Verbosity scales with `babble_rate`; injects DJ personality from `agent_md`.
- `MusicAssistantClient` — `services/music_assistant.py`. One-shot WS client: `play_media` (live queue) and `save_as_playlist` (DJ MP3s ingested as `library://track/<id>` via the builtin provider, then mixed with `apple_music://track/<id>` URIs into an MA playlist).

## Station config

| Field | Range | Effect |
|---|---|---|
| `length_minutes` | 5–480 | target total runtime |
| `dj_talk_rate` | 0..1 | probability of DJ patter between tracks |
| `dj_babble_rate` | 0..1 | 0 = "that was X by Y", 1 = stories + trivia |
| `dj_max_length_secs` | 5–120 | per-segment cap |
| `music_source` | string | seed text the LLM uses to assemble the tracklist |

## What's built

- FastAPI surface with CRUD for DJs / Stations / PlaylistRuns
- `POST /api/generate` — LLM track selection → catalog verification → DJ scripts → Piper synthesis
- `POST /api/runs/{id}/play` — push the playlist as a live queue to a Music Assistant player
- `POST /api/runs/{id}/save-to-ma` — save the playlist (with DJ patter) as an MA library playlist
- Web UI mounted at `/` — list stations, "Generate" button (does generate + save-to-ma)
- Alembic migration 001 (initial schema)
- Docker stack for server deploy via Portainer (`docker-compose.yml` builds the radiobot image, runs alembic on start, ships with the Bryce voices baked in)

## Notable backlog (GitHub issues)

#1 station variety sliders · #2 Music-Map · #3 MusicBrainz · #4 iOS Shortcut · #5 today-in-history chitchat · #6 nightly news scrape · #7 full create/edit web UI · #9 async generation · #10 length-budget top-up · #16 no-repeat memory · #18 Jellyfin/Lidarr · #19 mixed-source playback

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
