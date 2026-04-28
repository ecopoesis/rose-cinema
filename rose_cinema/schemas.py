from __future__ import annotations

from pydantic import BaseModel, Field


# ── DJ ─────────────────────────────────────────────────────────────────


class DJCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    agent_md: str = Field(default="")
    tts_provider: str = Field(default="piper")
    tts_voice_id: str = Field(default="en_US-lessac-medium")
    tts_voice_ref: str = Field(default="")


class DJUpdate(BaseModel):
    name: str | None = None
    agent_md: str | None = None
    tts_provider: str | None = None
    tts_voice_id: str | None = None
    tts_voice_ref: str | None = None


class DJResponse(BaseModel):
    id: str
    name: str
    agent_md: str
    tts_provider: str
    tts_voice_id: str
    tts_voice_ref: str


# ── Station ────────────────────────────────────────────────────────────


class StationCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    description: str = Field(default="")
    length_minutes: int = Field(default=60, ge=5, le=480)
    dj_talk_rate: float = Field(default=0.3, ge=0.0, le=1.0)
    dj_babble_rate: float = Field(default=0.5, ge=0.0, le=1.0)
    dj_max_length_secs: int = Field(default=30, ge=5, le=120)
    max_playlists: int = Field(default=0, ge=0)
    dj_id: str | None = None
    music_source: str = Field(default="")


class StationUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    length_minutes: int | None = Field(default=None, ge=5, le=480)
    dj_talk_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    dj_babble_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    dj_max_length_secs: int | None = Field(default=None, ge=5, le=120)
    max_playlists: int | None = Field(default=None, ge=0)
    dj_id: str | None = None
    music_source: str | None = None


class StationResponse(BaseModel):
    id: str
    name: str
    description: str
    length_minutes: int
    dj_talk_rate: float
    dj_babble_rate: float
    dj_max_length_secs: int
    max_playlists: int
    dj_id: str | None
    music_source: str
    album_art: str


# ── Test Playlist (track-only preview) ────────────────────────────────


class TestSongResponse(BaseModel):
    title: str
    artist: str
    album: str = ""
    year: str = ""
    apple_music_id: str = ""
    apple_music_url: str = ""
    duration_secs: float = 0.0


class TestPlaylistResponse(BaseModel):
    songs: list[TestSongResponse]
    generation_secs: float


# ── Playlist Generation ───────────────────────────────────────────────


class SongInput(BaseModel):
    title: str
    artist: str
    album: str = ""
    year: str = ""
    apple_music_id: str = ""
    duration_secs: float = 210.0


class GenerateRequest(BaseModel):
    station_id: str
    songs: list[SongInput] = Field(default_factory=list)


class PlaylistEntryResponse(BaseModel):
    type: str
    title: str = ""
    artist: str = ""
    album: str = ""
    year: str = ""
    apple_music_id: str = ""
    audio_file: str = ""
    script: str = ""
    duration_secs: float = 0.0


class ProgressResponse(BaseModel):
    total: int = 0
    completed: int = 0
    failed: int = 0
    current_step: str | None = None


class PlaylistRunResponse(BaseModel):
    id: str
    station_id: str
    status: str
    episode: int | None = None
    entries: list[PlaylistEntryResponse] = []
    error_message: str | None = None
    generation_secs: float | None = None
    progress: ProgressResponse | None = None


class EventResponse(BaseModel):
    id: str
    step_type: str
    step_index: int
    status: str
    error_message: str | None = None
    retry_count: int = 0
    created_at: str | None = None
    started_at: str | None = None
    completed_at: str | None = None


class PlaylistRunSummary(BaseModel):
    id: str
    station_id: str
    status: str
    episode: int | None = None
    created_at: str | None = None
    track_count: int = 0
    error_message: str | None = None
    generation_secs: float | None = None


# ── Music Assistant playback ──────────────────────────────────────────


class MAPlayRequest(BaseModel):
    player_id: str | None = None  # falls back to settings.ma_default_player_id
    option: str = "replace"  # replace | replace_next | next | add


class MAPlayResponse(BaseModel):
    player_id: str
    queued: int
    uris: list[str]


# ── Export / Import ───────────────────────────────────────────────────


class DJExport(BaseModel):
    name: str
    agent_md: str = ""
    tts_provider: str = "piper"
    tts_voice_id: str = "en_US-lessac-medium"
    tts_voice_ref: str = ""


class StationExport(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    description: str = ""
    length_minutes: int = Field(default=60, ge=5, le=480)
    dj_talk_rate: float = Field(default=0.3, ge=0.0, le=1.0)
    dj_babble_rate: float = Field(default=0.5, ge=0.0, le=1.0)
    dj_max_length_secs: int = Field(default=30, ge=5, le=120)
    max_playlists: int = Field(default=0, ge=0)
    music_source: str = ""
    dj_name: str | None = None
    album_art: str = ""


class ExportData(BaseModel):
    djs: list[DJExport] = []
    stations: list[StationExport] = []


class ImportResult(BaseModel):
    djs_created: int
    djs_skipped: int
    stations_created: int
