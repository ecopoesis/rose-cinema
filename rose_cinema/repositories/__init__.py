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


@dataclass
class PlaylistRunRecord:
    id: str = ""
    station_id: str = ""
    status: str = "pending"
    playlist_json: str = "[]"
    error_message: str | None = None
    ma_playlist_id: str | None = None
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
    async def create(self, record: PlaylistRunRecord) -> PlaylistRunRecord: ...

    @abstractmethod
    async def update(self, record: PlaylistRunRecord) -> PlaylistRunRecord: ...

    @abstractmethod
    async def delete(self, run_id: str) -> bool: ...
