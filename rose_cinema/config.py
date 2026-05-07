from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    # LLM
    llm_provider: str = Field(default="ollama")
    llm_base_url: str = Field(default="http://ollama:11434/v1")
    llm_model: str = Field(default="llama3.1:8b")
    llm_api_key: str = Field(default="not-needed")

    # TTS
    tts_provider: str = Field(default="piper")
    tts_api_key: str = Field(default="not-needed")
    chatterbox_url: str = Field(default="http://host.docker.internal:8004")

    # Database
    database_url: str = Field(default="postgresql+asyncpg://rose:rose@postgres:5432/rose_cinema")

    # Server
    host: str = Field(default="0.0.0.0")
    port: int = Field(default=8000)

    # Paths
    dj_audio_dir: str = Field(default="data/dj_audio")
    album_art_dir: str = Field(default="data/album_art")
    exports_dir: str = Field(default="data/exports")
    agents_dir: str = Field(default="agents")
    piper_data_dir: str = Field(default="data/piper_models")

    # MusicKit (Apple Music catalog) — leave blank to disable verification
    musickit_team_id: str = Field(default="")
    musickit_key_id: str = Field(default="")
    musickit_private_key_path: str = Field(default="")
    musickit_private_key: str = Field(default="")  # inline PEM (preferred over path)
    musickit_storefront: str = Field(default="us")

    # MusicBrainz tag enrichment — set to "RoseCinema/1.0 (you@email)" to enable
    musicbrainz_user_agent: str = Field(default="")

    # Music Assistant (playback)
    ma_url: str = Field(default="")           # e.g. http://server03.local:8095
    ma_token: str = Field(default="")
    ma_default_player_id: str = Field(default="")
    public_base_url: str = Field(default="")  # how MA reaches us for DJ audio

    # Icecast (listen/stream)
    icecast_host: str = Field(default="127.0.0.1")
    icecast_port: int = Field(default=8001)
    icecast_source_password: str = Field(default="hackme")
    squeezelite_path: str = Field(default="squeezelite")

    # Native streaming (direct Apple Music via Widevine)
    apple_music_user_token: str = Field(default="")
    widevine_cdm_dir: str = Field(default="/usr/local/bin/widevine_cdm")
    native_stream_bitrate: str = Field(default="192k")
    native_stream_metaint: int = Field(default=16384)

    # Track cache (pre-downloaded Apple Music files for ezstream)
    tracks_dir: str = Field(default="data/tracks")
    streams_dir: str = Field(default="data/streams")
    track_download_delay: float = Field(default=3.0)

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
