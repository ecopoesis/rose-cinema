from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


# ── DTOs (decoupled from ORM) ──────────────────────────────────────────


@dataclass
class DJRecord:
    id: str = ""
    name: str = ""
    agent_md: str = ""
    tts_provider: str = "piper"
    tts_voice_id: str = "en_US-lessac-medium"
    tts_voice_ref: str = ""


@dataclass
class StationRecord:
    id: str = ""
    name: str = ""
    description: str = ""
    length_minutes: int = 60
    dj_talk_rate: float = 0.3
    dj_babble_rate: float = 0.5
    dj_max_length_secs: int = 30
    max_playlists: int = 0
    dj_id: str | None = None
    music_source: str = ""
    source_artists: list[str] | None = None
    source_albums: list[str] | None = None
    source_tracks: list[str] | None = None
    album_art: str = ""
    cron_schedule: str | None = None


@dataclass
class PlaylistRunRecord:
    id: str = ""
    station_id: str = ""
    status: str = "pending"
    playlist_json: str = "[]"
    error_message: str | None = None
    episode: int | None = None
    ma_playlist_id: str | None = None
    generation_secs: float | None = None
    created_at: str | None = None


# ── Repository interfaces ──────────────────────────────────────────────


class DJRepository(ABC):
    @abstractmethod
    async def get(self, dj_id: str) -> DJRecord | None: ...

    @abstractmethod
    async def list_all(self) -> list[DJRecord]: ...

    @abstractmethod
    async def create(self, record: DJRecord) -> DJRecord: ...

    @abstractmethod
    async def update(self, record: DJRecord) -> DJRecord: ...

    @abstractmethod
    async def delete(self, dj_id: str) -> bool: ...


class StationRepository(ABC):
    @abstractmethod
    async def get(self, station_id: str) -> StationRecord | None: ...

    @abstractmethod
    async def list_all(self) -> list[StationRecord]: ...

    @abstractmethod
    async def create(self, record: StationRecord) -> StationRecord: ...

    @abstractmethod
    async def update(self, record: StationRecord) -> StationRecord: ...

    @abstractmethod
    async def delete(self, station_id: str) -> bool: ...


class PlaylistRunRepository(ABC):
    @abstractmethod
    async def get(self, run_id: str) -> PlaylistRunRecord | None: ...

    @abstractmethod
    async def list_by_station(self, station_id: str) -> list[PlaylistRunRecord]: ...

    @abstractmethod
    async def list_recent_track_ids(self, station_id: str, max_runs: int = 3) -> set[str]: ...

    @abstractmethod
    async def create(self, record: PlaylistRunRecord) -> PlaylistRunRecord: ...

    @abstractmethod
    async def update(self, record: PlaylistRunRecord) -> PlaylistRunRecord: ...

    @abstractmethod
    async def delete(self, run_id: str) -> bool: ...
