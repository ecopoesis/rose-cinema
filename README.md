# Rose Cinema 🎙️📻

AI-powered radio station generator for Apple Music. Creates personalized radio stations with AI DJs that introduce songs, tell stories, and bring personality to your listening experience.

## What It Does

Rose Cinema generates radio-style playlists that interleave your Apple Music tracks with AI-generated DJ segments. Each DJ has a unique personality (defined via `AGENT.md`), a TTS voice, and configurable chattiness.

**Playback modes:**
- **HomePod/AirPlay**: Live orchestration via `pyatv` — your server queues Apple Music tracks and DJ audio clips on your HomePods
- **Mobile/Remote**: API endpoint that iOS Shortcuts can hit to get playlists for on-the-go listening (works over Tailscale)

## Architecture

```
┌─────────────────────────────────────────────┐
│              rose-cinema (Docker)            │
│                                              │
│  ┌──────────┐  ┌──────────┐  ┌───────────┐  │
│  │ Station  │→│ DJ Script │→│   TTS     │  │
│  │ Builder  │  │ Generator │  │ Renderer  │  │
│  └──────────┘  └──────────┘  └───────────┘  │
│       │              │              │        │
│       ▼              ▼              ▼        │
│  ┌──────────┐  ┌──────────┐  ┌───────────┐  │
│  │ Apple    │  │   LLM    │  │  TTS      │  │
│  │ Music    │  │ Provider │  │  Provider │  │
│  │ Metadata │  │ (pluggable)│ │ (pluggable)│ │
│  └──────────┘  └──────────┘  └───────────┘  │
│                      │                       │
│              ┌───────┴────────┐              │
│              │   Ollama       │              │
│              │  (separate     │              │
│              │   container)   │              │
│              └────────────────┘              │
│                                              │
│  ┌──────────────┐  ┌─────────────────────┐   │
│  │  pyatv       │  │  REST API           │   │
│  │  (HomePod)   │  │  (Shortcuts/Web UI) │   │
│  └──────────────┘  └─────────────────────┘   │
└─────────────────────────────────────────────┘
```

## Configuration

### Station Parameters

| Parameter        | Type    | Description                                           |
|-----------------|---------|-------------------------------------------------------|
| `length`        | int     | Target playlist length in minutes                     |
| `dj_talk_rate`  | float   | 0.0 = DJ never talks, 1.0 = DJ talks between every song |
| `dj_babble_rate`| float   | 0.0 = just song/artist name, 1.0 = full stories/chitchat |
| `dj_max_length` | int     | Maximum DJ segment length in seconds                  |

### DJs

Each DJ is defined with:
- **Name** and display info
- **AGENT.md** — personality prompt (tone, style, catchphrases, musical knowledge)
- **TTS voice** — provider-specific voice ID
- **TTS provider** — which synthesis engine to use

## LLM Providers

Pluggable via OpenAI-compatible API:

| Provider   | Base URL                          | Notes                        |
|-----------|-----------------------------------|------------------------------|
| Ollama    | `http://ollama:11434/v1`          | Local, free, default         |
| Anthropic | `https://api.anthropic.com/v1/`   | OpenAI-compatible endpoint   |
| OpenAI    | `https://api.openai.com/v1`       | If you must                  |

## TTS Providers

| Provider     | Notes                                    |
|-------------|------------------------------------------|
| Piper       | Local, free, runs in container (default) |
| ElevenLabs  | Cloud, great quality, costs money        |
| OpenAI TTS  | Cloud, good quality, costs money         |

## Quick Start

```bash
# Clone and start
git clone <your-repo-url>
cd rose-cinema
cp .env.example .env  # Edit with your API keys if using cloud providers

# Start everything
docker compose up -d

# Pull an LLM model
docker compose exec ollama ollama pull llama3.1:8b

# Open the web UI
open http://localhost:8000
```

## Development

```bash
# Run migrations
docker compose exec radiobot alembic upgrade head

# Run tests
docker compose exec radiobot pytest
```

## License

MIT
