# Rose Cinema 🎙️📻

AI-powered radio station generator. Builds a candidate pool of real Apple Music tracks from a seed (artist, song, or theme — via MusicKit's `similar-artists` graph or genre charts), has an LLM curate the pool into an arc, generates DJ patter in a chosen personality, synthesizes voice with Piper, and either:

- **Saves** the result as a playlist inside [Music Assistant](https://music-assistant.io/) — visible in MA's UI, playable any time to any AirPlay/Sonos/Chromecast/webplayer endpoint, or
- **Plays** the result live by pushing the queue to a chosen MA player.

## How it fits together

```
            ┌────────────────────────────────────────────────┐
            │  rose-cinema (this repo)                       │
            │                                                │
            │  Web UI  ─►  /api/generate  ─►  StationBuilder │
            │                                  │             │
            │             ┌────────────────────┘             │
            │             ▼                                  │
            │   SeedPoolBuilder ──► MusicKit catalog         │
            │     (similar-artists graph / genre charts)     │
            │             │                                  │
            │   TrackPicker ──► LLM curates pool indices     │
            │                   (Ollama / OpenAI-compatible) │
            │             │                                  │
            │   DJScriptService ─► LLM (DJ patter)           │
            │             ▼                                  │
            │   PiperTTS  ─► data/dj_audio/*.mp3             │
            │             │                                  │
            │             ▼                                  │
            │   /api/runs/{id}/save-to-ma  ─►  Music         │
            │   /api/runs/{id}/play           Assistant      │
            └────────────────────────────────────────────────┘
                                                │
                            ┌───────────────────┴───────────────────┐
                            ▼                                       ▼
                    Apple Music tracks                  builtin://track/<dj-mp3-url>
                    (subscription, by ID)              (DJ patter, fetched from radiobot)
                            │                                       │
                            └───────────────► MA player ◄───────────┘
                                  (AirPlay / Sonos / webplayer / …)
```

## Status

What's working:

- ✅ **Hybrid track selection**: SeedPoolBuilder produces a catalog-grounded pool (artist's top-songs + similar-artists' top-songs, or theme→genre charts); LLM curates indices from the pool. Per-artist cap (2) plus deterministic top-up keeps playlists at target length even when the LLM piles up favorites. Hallucination-impossible by construction.
- ✅ DJ scripts scaled by `babble_rate`, voiced via Piper (incl. Bryce Beattie's narrator set: `cori-high`, `kristin`, `bryce`, `norman`, `mv2`, `jenny`)
- ✅ Music Assistant integration — save full playlist (DJs + Apple Music) as an MA library playlist; or push directly to a player queue
- ✅ FastAPI + SQLite + Alembic; web UI at `/` (list stations, "Generate" button)
- ✅ Three Docker stacks for production deploy: `music-assistant` (playback), `ollama` (LLM + Open WebUI for browser chat), `rose-cinema` (this app). Each is its own Portainer stack from this repo.

In flight (see GitHub issues):

- 🚧 #1 Station variety sliders (genre / year / popularity)
- 🚧 #9 Async generation (today `/api/generate` blocks for the full pipeline)
- 🚧 #4 iOS Shortcut for "Hey Siri, start <station>"
- 🚧 #5 / #6 Topical DJ chitchat (today-in-history + nightly news scrape)
- 🚧 #18 / #19 Jellyfin + Lidarr + non-Apple-Music playback
- 🚧 #2 / #3 Music-Map and MusicBrainz integration

Full backlog: <https://github.com/ecopoesis/rose-cinema/issues>

## Architecture decisions

- **LLM**: any OpenAI-compatible chat completions endpoint. Ollama by default (local, free). Anthropic / OpenAI / OpenRouter etc. work by swapping `LLM_BASE_URL` + `LLM_API_KEY` + `LLM_MODEL`.
- **Apple Music catalog**: MusicKit REST API. Developer JWT (ES256, 90-day lifetime, lazy-cached) signed with a `.p8` key. Used **only for read** — track verification + canonical metadata.
- **TTS**: Piper, runs in-process via the `piper` CLI. Voices live under `data/piper_models/`. (ElevenLabs / OpenAI TTS providers exist in code but the install path bakes Piper into the Docker image.)
- **Playback**: Music Assistant runs as a separate container/stack on the same machine (or LAN). `radiobot` talks to it over the WebSocket API; MA owns all the actual audio routing.
- **Database**: SQLite via SQLAlchemy async + Alembic. Repository-pattern abstractions in `rose_cinema/repositories/__init__.py`; SQLite implementations in `rose_cinema/repositories/sqlite.py`. Postgres is a drop-in if/when needed.
- **DJ personalities**: Markdown blob in the `djs.agent_md` column. Two samples in `agents/`: Velvet (late-night) and Spark (morning drive).

## Station config

| Field | Range | Effect |
|---|---|---|
| `length_minutes` | 5–480 | target total runtime |
| `dj_talk_rate` | 0..1 | probability DJ talks between any two tracks |
| `dj_babble_rate` | 0..1 | 0 = "that was X by Y", 1 = stories + trivia |
| `dj_max_length_secs` | 5–120 | per-segment cap |
| `music_source` | string | seed text the LLM uses to assemble the tracklist |

## Quick start — production (Linux server + Portainer)

This is what's deployed to `server03` today.

1. **Add the Music Assistant stack** in Portainer pointing at `deploy/music-assistant/docker-compose.yml`. Bring it up, open `http://<host>:8095/`, create an admin user, add the **Apple Music** provider (signs in with your Apple ID), add players (Sonos / AirPlay / webplayer / etc.).
2. **Generate an MA API token** in Settings → General → Security. You'll need it next.
3. **Add the Ollama stack** in Portainer pointing at `deploy/ollama/docker-compose.yml`. Brings up `ollama` (host networking, port 11434) and `open-webui` for browser-side chat at `http://<host>:3000/`.
4. **Add the rose-cinema stack** in Portainer pointing at `docker-compose.yml` (repo root) with these env vars:

   ```
   MUSICKIT_TEAM_ID=...
   MUSICKIT_KEY_ID=...
   MUSICKIT_PRIVATE_KEY=<base64-encoded contents of AuthKey_XXX.p8>
   MUSICKIT_STOREFRONT=us
   MA_URL=http://host.docker.internal:8095
   MA_TOKEN=<JWT from step 2>
   MA_DEFAULT_PLAYER_ID=<player_id from MA's API or UI>
   PUBLIC_BASE_URL=http://<your-server>:8765   # how MA fetches DJ MP3s back from radiobot
   ```

5. Pull the LLM model into the Ollama stack (one-time, ~21 GB):

   ```bash
   docker exec ollama ollama pull qwen3.6:35b-a3b
   ```

   This is the MoE variant of Qwen3.6 — 35B total parameters but only 3B active per token, so per-token CPU inference is roughly an order of magnitude faster than the dense `qwen3.6:27b` / `:35b` variants.

6. Bootstrap a DJ and a station (until #7 builds the create-station UI):

   ```bash
   curl -X POST http://<host>:8765/api/djs \
     -H 'Content-Type: application/json' \
     -d '{"name":"Velvet","agent_md":"...","tts_voice_id":"jenny"}'
   curl -X POST http://<host>:8765/api/stations \
     -H 'Content-Type: application/json' \
     -d '{"name":"Late Night","length_minutes":30,"dj_talk_rate":0.5,
          "dj_babble_rate":0.5,"dj_max_length_secs":30,
          "dj_id":"<uuid from previous>",
          "music_source":"Welcome to the Black Parade by My Chemical Romance"}'
   ```

7. Open `http://<host>:8765/` and click **Generate** on the station. The generated playlist appears in Music Assistant a few minutes later.

## Quick start — local dev (macOS)

Music Assistant **does not run cleanly in Docker on macOS** (mDNS doesn't traverse Docker Desktop's NAT). Either point at an MA instance running on a real Linux box, or skip MA locally and just exercise generation.

```bash
# Python toolchain
brew install pyenv ollama ffmpeg
pyenv install 3.12.1
pyenv local 3.12.1
python -m venv .venv
.venv/bin/pip install -r requirements.txt

# LLM (native Ollama gets Metal GPU acceleration on Apple Silicon)
brew services start ollama
ollama pull qwen3:30b-a3b-instruct-2507-q4_K_M

# Piper voice (default fallback; Bryce Beattie voices download separately)
mkdir -p data/piper_models data/dj_audio data/exports
cd data/piper_models && python -m piper.download_voices en_US-lessac-medium && cd -

# Config
cp .env.example .env   # then edit (see Issue #14 — .env.example doesn't exist yet; copy from below)
.venv/bin/alembic upgrade head

# Run
.venv/bin/uvicorn rose_cinema.api:app --host 0.0.0.0 --port 8765
```

Sample `.env` for local dev:

```env
LLM_BASE_URL=http://localhost:11434/v1
LLM_MODEL=qwen3:30b-a3b-instruct-2507-q4_K_M
LLM_API_KEY=ollama

TTS_PROVIDER=piper

MUSICKIT_TEAM_ID=BKY36YMQER
MUSICKIT_KEY_ID=XXXXXXXXXX
MUSICKIT_PRIVATE_KEY_PATH=.keys/AuthKey_XXXXXXXXXX.p8
MUSICKIT_STOREFRONT=us

MA_URL=http://<linux-box>.local:8095
MA_TOKEN=<jwt>
MA_DEFAULT_PLAYER_ID=<player_id>
PUBLIC_BASE_URL=http://<your-mac-ip>:8765
```

## Tests

```bash
.venv/bin/pytest tests/
```

Coverage is currently the dataclass + DJScriptService surface. Expanding to `TrackPicker`, MusicKit signer/parser, and the MA client is tracked by [#15](https://github.com/ecopoesis/rose-cinema/issues/15).

## Code style

- Python 3.12, `from __future__ import annotations`, type hints everywhere
- Async throughout (SQLAlchemy async, FastAPI async, httpx async, websockets)
- Pydantic for API schemas, pydantic-settings for config
- ORM types stay behind the repository boundary — interfaces return DTOs
- No comments unless the *why* is non-obvious

## License

MIT
