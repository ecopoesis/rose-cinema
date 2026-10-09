# Rose Cinema 🎙️📻

AI-powered radio station generator. Builds a candidate pool of real Apple Music tracks from a seed (artist, song, or theme — via MusicKit's `similar-artists` graph or genre charts), has an LLM (Claude Sonnet 5.5 via the Anthropic API by default; Ollama or any OpenAI-compatible endpoint as an alternative) curate the pool into an arc, generates DJ patter in a chosen personality, synthesizes voice with Piper, and either:

- **Saves** the result as a playlist inside [Music Assistant](https://music-assistant.io/) — visible in MA's UI, playable any time to any AirPlay/Sonos/Chromecast/webplayer endpoint, or
- **Plays** the result live by pushing the queue to a chosen MA player.

## How it fits together

```
            ┌─────────────────────────────────────────────────────┐
            │  rose-cinema (this repo)                            │
            │                                                     │
            │  Web UI  ─►  /api/generate  ─►  EventQueue (PG)    │
            │                                  │                  │
            │             ┌────────────────────┘                  │
            │             ▼                                       │
            │   QueueWorker picks up steps via LISTEN/NOTIFY      │
            │             │                                       │
            │   pick_tracks ──► SeedPoolBuilder + TrackPicker     │
            │             │       (MusicKit catalog + LLM)        │
            │             ▼                                       │
            │   generate_*_script ──► DJScriptService (LLM)      │
            │             ▼                                       │
            │   synthesize_* ──► PiperTTS ─► data/dj_audio/*.mp3 │
            │             ▼                                       │
            │   finalize_playlist ──► ma_ingest/create/add_tracks │
            │                                                     │
            │   LLM = Anthropic API (default) or Ollama/OpenAI-   │
            │   compatible; token counts land on playlist_runs    │
            │                              ▼                      │
            │                        Music Assistant              │
            └─────────────────────────────────────────────────────┘
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
- ✅ FastAPI + PostgreSQL + Alembic; web UI at `/` (list stations, "Generate" button with live progress)
- ✅ Queue-based generation with PG LISTEN/NOTIFY — crash-recoverable, retry up to 3×, chain dispatch
- ✅ LLM via the native Anthropic API (`claude-sonnet-5-5`) by default, or a local Ollama / any OpenAI-compatible endpoint
- ✅ Per-run LLM token accounting: `GET /api/runs/{id}` and `GET /api/stations/{id}/runs` return `llm_input_tokens` / `llm_output_tokens` (covers completed and failed step attempts, retries included; can undercount if the worker is killed mid-step, and `test-playlist` previews are not counted)
- ✅ Docker stacks for production deploy: `music-assistant` (playback), `rose-cinema` (this app + PostgreSQL), and optionally `ollama` (local LLM + Open WebUI for browser chat). Each is its own Portainer stack from this repo.

In flight (see GitHub issues):

- 🚧 #1 Station variety sliders (genre / year / popularity)
- 🚧 #4 iOS Shortcut for "Hey Siri, start <station>"
- 🚧 #5 / #6 Topical DJ chitchat (today-in-history + nightly news scrape)
- 🚧 #18 / #19 Jellyfin + Lidarr + non-Apple-Music playback
- 🚧 #2 / #3 Music-Map and MusicBrainz integration

Full backlog: <https://github.com/ecopoesis/rose-cinema/issues>

## Architecture decisions

- **LLM**: two provider paths, chosen by `LLM_PROVIDER`.
  - `anthropic` (default) — native Messages API through the official `anthropic` SDK, model `claude-sonnet-5-5`. Adaptive thinking is always on; depth is set with `LLM_EFFORT` (default `low`). Sampling parameters are not sent (the model rejects them), and requests opt into Anthropic's server-side refusal fallback. Needs an API key: `ANTHROPIC_API_KEY`, or a key file at `ANTHROPIC_API_KEY_FILE`.
  - anything else (e.g. `ollama`) — any OpenAI-compatible chat completions endpoint via `LLM_BASE_URL` + `LLM_API_KEY` + `LLM_MODEL`. Ollama (local, free) is the tested alternative; OpenAI / OpenRouter work the same way.

  | Env var | Default | Effect |
  |---|---|---|
  | `LLM_PROVIDER` | `anthropic` | `anthropic` = native Anthropic API; any other value = OpenAI-compatible endpoint |
  | `LLM_MODEL` | `claude-sonnet-5-5` | model ID for the chosen provider |
  | `LLM_EFFORT` | `low` | Anthropic only: `low` / `medium` / `high` / `xhigh` / `max` |
  | `ANTHROPIC_API_KEY` | *(empty)* | Anthropic key; wins over the key file |
  | `ANTHROPIC_API_KEY_FILE` | *(empty; `/run/secrets/anthropic_api_key` in Docker)* | path to a file holding the key, used when `ANTHROPIC_API_KEY` is empty. A missing path, a directory, or an empty file counts as "no key" |
  | `ANTHROPIC_API_KEY_HOST_FILE` | `/home/miker/.rose-cinema-anthropic` | docker-compose only: host file bind-mounted read-only at `/run/secrets/anthropic_api_key` |
  | `LLM_BASE_URL` | `http://ollama:11434/v1` | OpenAI-compatible path only |
  | `LLM_API_KEY` | `not-needed` | OpenAI-compatible path only |
- **Apple Music catalog**: MusicKit REST API. Developer JWT (ES256, 90-day lifetime, lazy-cached) signed with a `.p8` key. Used **only for read** — track verification + canonical metadata.
- **TTS**: Piper, runs in-process via the `piper` CLI. Voices live under `data/piper_models/`. (ElevenLabs / OpenAI TTS providers exist in code but the install path bakes Piper into the Docker image.)
- **Playback**: Music Assistant runs as a separate container/stack on the same machine (or LAN). `radiobot` talks to it over the WebSocket API; MA owns all the actual audio routing.
- **Similar artists**: Apple Music's static `similar-artists` view blended with [ListenBrainz Labs](https://labs.api.listenbrainz.org/) similar-artists (free, no key, keyed by MusicBrainz MBID). Results are lazily cached in Postgres (`lb_similar_cache`, 30 days; artist-name → MBID/Apple-ID links in `artist_links`). On by default; set `LISTENBRAINZ_ENABLED=false` to disable. Requires `MUSICBRAINZ_USER_AGENT` to be set for MBID resolution.
- **Deep cuts**: a local [MusicBrainz DB-only mirror](deploy/musicbrainz/README.md) (~100 GB Postgres, hourly replication) supplies full discographies; candidates are resolved to playable Apple Music tracks via catalog search and cached permanently in `recording_resolutions`. Enabled by setting `MUSICBRAINZ_DB_URL`; without it, `popularity_variety` only steers the LLM prompt.
- **Database**: PostgreSQL 17 via SQLAlchemy async (asyncpg) + Alembic. Repository-pattern abstractions in `rose_cinema/repositories/__init__.py`; SQL implementations in `rose_cinema/repositories/sql.py`. PG LISTEN/NOTIFY drives the generation queue.
- **DJ personalities**: Markdown blob in the `djs.agent_md` column. Two samples in `agents/`: Velvet (late-night) and Spark (morning drive).

## Station config

| Field | Range | Effect |
|---|---|---|
| `length_minutes` | 5–480 | target total runtime |
| `dj_talk_rate` | 0..1 | probability DJ talks between any two tracks |
| `dj_babble_rate` | 0..1 | 0 = "that was X by Y", 1 = stories + trivia |
| `dj_max_length_secs` | 5–120 | per-segment cap |
| `music_source` | string | seed text the LLM uses to assemble the tracklist |
| `genre_variety` | 0..1 | 0 = stay in the seed's genre, 1 = wander into adjacent genres freely |
| `year_variety` | 0..1 | 0 = seed's era only (hard ±5-year window), 1 = any decade |
| `popularity_variety` | 0..1 | 0 = hits and top songs, 1 = deep cuts & B-sides (full effect needs the [MusicBrainz mirror](deploy/musicbrainz/README.md); prompt-only otherwise) |
| `excluded_artists` | string list | artists never played on this station — not even as a featured guest or collaborator; cumulative with the global list (`PUT /api/settings/exclusions`, editable via "Excluded Artists" in the web UI) |

## Quick start — production (Linux server + Portainer)

> **Upgrading an existing deployment:** the default provider is now `anthropic`. If your stack sets `LLM_MODEL` to an Ollama model (e.g. `qwen3:…`), either change it to a Claude model / remove it, or set `LLM_PROVIDER=ollama` — otherwise generation fails with a provider/model mismatch error.

This is what's deployed to `server03` today.

1. **Add the Music Assistant stack** in Portainer pointing at `deploy/music-assistant/docker-compose.yml`. Bring it up, open `http://<host>:8095/`, create an admin user, add the **Apple Music** provider (signs in with your Apple ID), add players (Sonos / AirPlay / webplayer / etc.).
2. **Generate an MA API token** in Settings → General → Security. You'll need it next.
3. **Provide an Anthropic API key.** Either put the key (just the key, one line) in a file on the host — the stack bind-mounts `/home/miker/.rose-cinema-anthropic` read-only by default; set `ANTHROPIC_API_KEY_HOST_FILE` to use another path — or set `ANTHROPIC_API_KEY` as a stack env var (the env var wins). Create the file *before* starting the stack: Docker creates an empty directory at a missing bind-mount source, which rose-cinema treats as "no key". If that already happened (the file was missing at first start), `rmdir` the directory on the host, create the file, and restart the stack. The file must be a single line containing only the key.

   ```bash
   install -m 600 /dev/null ~/.rose-cinema-anthropic && $EDITOR ~/.rose-cinema-anthropic
   ```

4. **Add the rose-cinema stack** in Portainer pointing at `docker-compose.yml` (repo root). This brings up PostgreSQL 17 + the radiobot app. Set these env vars:

   ```
   POSTGRES_PASSWORD=<choose a password>
   MUSICKIT_TEAM_ID=...
   MUSICKIT_KEY_ID=...
   MUSICKIT_PRIVATE_KEY=<base64-encoded contents of AuthKey_XXX.p8>
   MUSICKIT_STOREFRONT=us
   MA_URL=http://<server-ip>:8095
   MA_TOKEN=<JWT from step 2>
   MA_DEFAULT_PLAYER_ID=<player_id from MA's API or UI>
   PUBLIC_BASE_URL=http://<your-server>:8765   # how MA fetches DJ MP3s back from radiobot
   CHATTERBOX_URL=http://<server-ip>:8004      # optional: Chatterbox TTS server
   ANTHROPIC_API_KEY_HOST_FILE=/path/to/keyfile # optional: host path of the Anthropic key file
   ANTHROPIC_API_KEY=sk-ant-...                 # optional: alternative to the key file
   ```

5. Open `http://<host>:8765/`, create a DJ and a station via the web UI, and click **Generate**. The button shows live progress; the playlist appears in Music Assistant once complete.

### Alternative: local LLM with Ollama

To run without the Anthropic API, add the Ollama stack in Portainer pointing at `deploy/ollama/docker-compose.yml` (brings up `ollama` on host networking, port 11434, and `open-webui` at `http://<host>:3000/`), set these on the rose-cinema stack:

   ```
   LLM_PROVIDER=ollama
   LLM_MODEL=qwen3:30b-a3b-instruct-2507-q4_K_M
   ```

and pull the model (one-time, ~18 GB):

   ```bash
   docker exec ollama ollama pull qwen3:30b-a3b-instruct-2507-q4_K_M
   ```

   This is the Qwen3 30B MoE — 30B total parameters but only 3B active per token, so per-token CPU inference is roughly an order of magnitude faster than dense models of comparable quality. The newer `qwen3.6:35b-a3b` exists but its `q4_K_M` quant needs ~25 GiB to load — too big for a typical 16-32 GiB server unless you have GPU offload.

## Quick start — local dev (macOS)

Music Assistant **does not run cleanly in Docker on macOS** (mDNS doesn't traverse Docker Desktop's NAT). Either point at an MA instance running on a real Linux box, or skip MA locally and just exercise generation.

```bash
# Python toolchain
brew install pyenv ffmpeg postgresql@17
pyenv install 3.12.1
pyenv local 3.12.1
python -m venv .venv
.venv/bin/pip install -r requirements.txt

# PostgreSQL
brew services start postgresql@17
createdb rose_cinema

# LLM: put your Anthropic API key (one line) in a git-ignored file at the repo root
$EDITOR .rose-cinema-anthropic

# Piper voice (default fallback; Bryce Beattie voices download separately)
mkdir -p data/piper_models data/dj_audio data/exports
cd data/piper_models && python -m piper.download_voices en_US-lessac-medium && cd -

# Config
$EDITOR .env           # create it from the sample below
.venv/bin/alembic upgrade head

# Run
.venv/bin/uvicorn rose_cinema.api:app --host 0.0.0.0 --port 8765
```

Sample `.env` for local dev:

```env
DATABASE_URL=postgresql+asyncpg://localhost/rose_cinema

LLM_PROVIDER=anthropic
LLM_MODEL=claude-sonnet-5-5
ANTHROPIC_API_KEY_FILE=.rose-cinema-anthropic   # or ANTHROPIC_API_KEY=sk-ant-...

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

To use a local Ollama instead (native Ollama gets Metal GPU acceleration on Apple Silicon), `brew install ollama && brew services start ollama && ollama pull qwen3:30b-a3b-instruct-2507-q4_K_M`, then replace the three LLM lines with:

```env
LLM_PROVIDER=ollama
LLM_BASE_URL=http://localhost:11434/v1
LLM_MODEL=qwen3:30b-a3b-instruct-2507-q4_K_M
LLM_API_KEY=ollama
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
