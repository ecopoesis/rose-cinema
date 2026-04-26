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

    # Database
    database_url: str = Field(default="sqlite+aiosqlite:///data/rose_cinema.db")

    # Server
    host: str = Field(default="0.0.0.0")
    port: int = Field(default=8000)

    # Paths
    dj_audio_dir: str = Field(default="data/dj_audio")
    exports_dir: str = Field(default="data/exports")
    agents_dir: str = Field(default="agents")
    piper_data_dir: str = Field(default="data/piper_models")

    # MusicKit (Apple Music catalog) — leave blank to disable verification
    musickit_team_id: str = Field(default="")
    musickit_key_id: str = Field(default="")
    musickit_private_key_path: str = Field(default="")
    musickit_private_key: str = Field(default="")  # inline PEM (preferred over path)
    musickit_storefront: str = Field(default="us")

    # Music Assistant (playback)
    ma_url: str = Field(default="")           # e.g. http://server03.local:8095
    ma_token: str = Field(default="")
    ma_default_player_id: str = Field(default="")
    public_base_url: str = Field(default="")  # how MA reaches us for DJ audio

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
