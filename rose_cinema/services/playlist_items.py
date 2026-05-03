from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def build_ma_media_items(
    playlist_json: str,
    station_name: str,
    dj_name: str,
    base_url: str,
    art_url: str | None,
    episode: int | None,
    start_index: int = 0,
) -> list[str | dict]:
    entries = json.loads(playlist_json)
    items: list[str | dict] = []
    dj_part = 0
    for i, e in enumerate(entries):
        if e.get("type") == "dj":
            dj_part += 1
        if i < start_index:
            continue
        if e.get("type") == "song" and e.get("apple_music_id"):
            items.append(f"apple_music://track/{e['apple_music_id']}")
        elif e.get("type") == "dj" and e.get("audio_file"):
            fname = Path(e["audio_file"]).name
            url = f"{base_url}/audio/{fname}"
            if episode and station_name:
                seg_name = (
                    f"{station_name} - Episode {episode} - Intro"
                    if dj_part <= 1
                    else f"{station_name} - Episode {episode} - Part {dj_part - 1}"
                )
            else:
                seg_name = f"{station_name} - DJ {dj_part}" if station_name else f"DJ Segment {dj_part}"
            item: dict = {
                "uri": url,
                "item_id": url,
                "provider": "builtin",
                "media_type": "track",
                "name": seg_name,
                "artists": [{
                    "name": dj_name,
                    "item_id": dj_name,
                    "provider": "builtin",
                    "media_type": "artist",
                }],
                "metadata": {},
            }
            if art_url:
                item["metadata"]["images"] = [
                    {"type": "thumb", "path": art_url, "provider": "builtin", "remotely_accessible": True}
                ]
            items.append(item)
        else:
            logger.warning(
                "Skipping unplayable entry: type=%s id=%s file=%s",
                e.get("type"), e.get("apple_music_id"), e.get("audio_file"),
            )
    return items
