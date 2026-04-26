from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class CatalogTrack:
    apple_music_id: str
    title: str
    artist: str
    album: str = ""
    year: str = ""
    duration_secs: float = 0.0


class MusicCatalog(ABC):
    """A music catalog that can verify whether a track exists and resolve canonical metadata."""

    @abstractmethod
    async def search(self, query: str, limit: int = 5) -> list[CatalogTrack]: ...
