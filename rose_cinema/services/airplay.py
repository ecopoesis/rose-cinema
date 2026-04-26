from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from rose_cinema.services.station_builder import PlaylistEntry

logger = logging.getLogger(__name__)


@dataclass
class AirPlayDevice:
    name: str
    identifier: str
    address: str


class AirPlayService:
    """
    Discovers and controls AirPlay devices (HomePods, Apple TVs) via pyatv.
    Plays back generated playlists by alternating between Apple Music tracks
    and local DJ audio segments.
    """

    async def discover_devices(self, timeout: float = 5.0) -> list[AirPlayDevice]:
        """Scan the local network for AirPlay devices."""
        try:
            import pyatv

            devices = await pyatv.scan(asyncio.get_event_loop(), timeout=timeout)
            return [
                AirPlayDevice(
                    name=d.name,
                    identifier=str(d.identifier),
                    address=str(d.address),
                )
                for d in devices
            ]
        except ImportError:
            logger.warning("pyatv not installed — AirPlay discovery unavailable")
            return []
        except Exception:
            logger.exception("AirPlay discovery failed")
            return []

    async def play_playlist(
        self,
        device_id: str,
        entries: list[PlaylistEntry],
    ) -> None:
        """
        Play a playlist on an AirPlay device.
        For songs: sends Apple Music track ID to the device.
        For DJ segments: streams the local audio file.

        This is a long-running coroutine — it plays through the whole list.
        """
        try:
            import pyatv

            devices = await pyatv.scan(asyncio.get_event_loop(), timeout=5)
            target = next(
                (d for d in devices if str(d.identifier) == device_id), None
            )
            if not target:
                raise ValueError(f"Device {device_id} not found on network")

            atv = await pyatv.connect(target, asyncio.get_event_loop())
            try:
                for entry in entries:
                    if entry.type == "song" and entry.apple_music_id:
                        # Play Apple Music track via MRP protocol
                        logger.info("Playing song: %s - %s", entry.artist, entry.title)
                        # TODO: implement Apple Music playback via MusicKit/pyatv
                        # This requires pairing and auth — placeholder for now
                        await asyncio.sleep(entry.duration_secs)
                    elif entry.type == "dj" and entry.audio_file:
                        logger.info("Playing DJ segment: %s", entry.audio_file[:50])
                        # Stream local audio to AirPlay device
                        audio = atv.stream
                        await audio.stream_file(entry.audio_file)
                        # Wait for playback to finish
                        await asyncio.sleep(2)
            finally:
                atv.close()

        except ImportError:
            logger.error("pyatv not installed — cannot play to AirPlay devices")
            raise
