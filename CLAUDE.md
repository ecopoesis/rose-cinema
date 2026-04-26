# Rose Cinema — Project Context

AI-powered radio station generator for Apple Music. Creates playlists that interleave Apple Music tracks with AI-generated DJ segments (voice + personality).

## Architecture Decisions

- **Docker Compose**: two services — `radiobot` (Python/FastAPI) and `ollama` (shared LLM inference container, reusable by other apps)
- **LLM**: All providers use OpenAI-compatible chat completions API. Ollama is default (local, free). Anthropic and OpenAI work by changing `base_url` + `api_key` + `model` in `.env`. Anthropic exposes an OpenAI-compatible endpoint at `https://api.anthropic.com/v1/`.
- **TTS**: Pluggable — Piper (local/free, default), ElevenLabs (cloud), OpenAI TTS (cloud). Each DJ can have its own provider + voice.
- **Database**: SQLite now via SQLAlchemy async + Alembic migrations. Repository pattern with abstract interfaces in `repositories/__init__.py` and SQLite implementations in `repositories/sqlite.py`. Designed to swap to Postgres later — just write new repository implementations.
- **Playback**: `pyatv` for HomePod/AirPlay orchestration on the LAN. Server controls HomePods directly. For mobile (iPhone away from home), plan is iOS Shortcuts hitting the API over Tailscale.
- **DJ Personalities**: Each DJ has an `AGENT.md` stored in the `agent_md` column. Two samples in `agents/` dir: Velvet (late-night) and Spark (morning drive).

## Key Abstractions

- `LLMProvider` / `TTSProvider` — abstract base classes in `providers/__init__.py`
- `DJRepository` / `StationRepository` / `PlaylistRunRepository` — abstract in `repositories/__init__.py`
- `StationBuilder` — orchestrator in `services/station_builder.py`: picks songs → rolls dice on DJ segments → generates scripts via LLM → synthesizes via TTS → returns playlist
- `DJScriptService` — prompt engineering in `services/dj_script.py`: scales verbosity with `babble_rate`, injects DJ personality from `agent_md`

## Station Config Parameters

- `length_minutes`: target playlist duration
- `dj_talk_rate` (0..1): probability DJ talks between any two songs
- `dj_babble_rate` (0..1): 0 = just song/artist, 1 = stories and chitchat
- `dj_max_length_secs`: cap on any single DJ segment

## What's Built

- Full REST API (FastAPI) with CRUD for DJs, Stations, PlaylistRuns
- Playlist generation endpoint (`POST /api/generate`)
- AirPlay device discovery (`GET /api/airplay/devices`)
- Alembic migration 001 (initial schema)
- Provider implementations: OpenAI-compat LLM, Piper/ElevenLabs/OpenAI TTS
- Tests in `tests/test_core.py`

## What's Not Built Yet

- Web UI for managing DJs/stations and triggering generation
- Apple Music catalog search (MusicKit REST API integration)
- iOS Shortcut integration for mobile playback
- Actual pyatv Apple Music playback (currently placeholder — needs pairing/auth work)
- Background task queue for async playlist generation

## Commands

```bash
docker compose up -d
docker compose exec ollama ollama pull llama3.1:8b
docker compose exec radiobot alembic upgrade head
docker compose exec radiobot pytest
```

## Code Style

- Python 3.12, type hints everywhere, `from __future__ import annotations`
- Async throughout (SQLAlchemy async, FastAPI async endpoints)
- Pydantic for API schemas, pydantic-settings for config
- No ORMs leaking past repository boundaries — DTOs only
