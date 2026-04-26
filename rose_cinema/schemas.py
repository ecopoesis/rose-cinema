from __future__ import annotations

from pydantic import BaseModel, Field


# ── DJ ─────────────────────────────────────────────────────────────────


class DJCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    agent_md: str = Field(default="")
    tts_provider: str = Field(default="piper")
    tts_voice_id: str = Field(default="en_US-lessac-medium")


class DJUpdate(BaseModel):
    name: str | None = None
    agent_md: str | None = None
    tts_provider: str | None = None
    tts_voice_id: str | None = None


class DJResponse(BaseModel):
    id: str
    name: str
    agent_md: str
    tts_provider: str
    tts_voice_id: str


# ── Station ────────────────────────────────────────────────────────────


class StationCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    description: str = Field(default="")
    length_minutes: int = Field(default=60, ge=5, le=480)
    dj_talk_rate: float = Field(default=0.3, ge=0.0, le=1.0)
    dj_babble_rate: float = Field(default=0.5, ge=0.0, le=1.0)
    dj_max_length_secs: int = Field(default=30, ge=5, le=120)
    dj_id: str | None = None
    music_source: str = Field(default="")


class StationUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    length_minutes: int | None = Field(default=None, ge=5, le=480)
    dj_talk_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    dj_babble_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    dj_max_length_secs: int | None = Field(default=None, ge=5, le=120)
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
    dj_id: str | None
    music_source: str


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
    songs: list[SongInput] = Field(..., min_length=1)


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


class PlaylistRunResponse(BaseModel):
    id: str
    station_id: str
    status: str
    entries: list[PlaylistEntryResponse] = []
    error_message: str | None = None


# ── AirPlay ───────────────────────────────────────────────────────────


class AirPlayDeviceResponse(BaseModel):
    name: str
    identifier: str
    address: str


class PlayRequest(BaseModel):
    device_id: str
    run_id: str
