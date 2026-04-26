from __future__ import annotations

import logging
import random

from rose_cinema.providers import LLMMessage, LLMProvider
from rose_cinema.repositories import DJRecord

logger = logging.getLogger(__name__)


def _estimate_word_count(max_seconds: float, babble_rate: float) -> int:
    """Estimate target word count from duration. Average speech ≈ 150 wpm."""
    wpm = 150
    # Scale duration by babble_rate: low babble → short script
    effective_seconds = max(3, max_seconds * max(0.1, babble_rate))
    return int((effective_seconds / 60) * wpm)


def build_system_prompt(dj: DJRecord, babble_rate: float, max_seconds: int) -> str:
    word_count = _estimate_word_count(max_seconds, babble_rate)

    base = (
        "You are a radio DJ. Your job is to introduce songs, provide transitions, "
        "and entertain listeners between tracks.\n\n"
        "RULES:\n"
        "- Speak naturally as if on air. No stage directions, no sound effects in brackets.\n"
        "- Output ONLY what you would say out loud. No metadata, no labels.\n"
        f"- Keep your response to approximately {word_count} words.\n"
        "- Never say anything offensive or controversial.\n"
        "- Don't make up facts about songs. If you don't know, be vague.\n"
    )

    if babble_rate < 0.2:
        base += (
            "\nSTYLE: Be extremely brief. Just the song name and artist, maybe one short sentence.\n"
            "Example: 'That was Bohemian Rhapsody by Queen. Up next, Hotel California by the Eagles.'\n"
        )
    elif babble_rate < 0.5:
        base += (
            "\nSTYLE: Be concise but add a little personality. A quick fact or short comment.\n"
        )
    elif babble_rate < 0.8:
        base += (
            "\nSTYLE: Be conversational. Share an interesting fact, a short story, "
            "or your take on the music. Be engaging.\n"
        )
    else:
        base += (
            "\nSTYLE: Be a full entertainer. Tell stories, share trivia, riff on the music, "
            "connect songs thematically. Be the DJ people tune in for.\n"
        )

    # Append the DJ's personality prompt
    if dj.agent_md.strip():
        base += f"\nYOUR PERSONALITY:\n{dj.agent_md}\n"

    return base


class DJScriptService:
    def __init__(self, llm: LLMProvider):
        self._llm = llm

    async def generate_intro(
        self,
        dj: DJRecord,
        station_name: str,
        babble_rate: float,
        max_seconds: int,
    ) -> str:
        """Generate a station intro/greeting."""
        system = build_system_prompt(dj, babble_rate, max_seconds)
        user = (
            f"You're opening your radio show '{station_name}'. "
            "Give a welcoming intro to kick things off."
        )
        return await self._llm.complete(
            messages=[LLMMessage("system", system), LLMMessage("user", user)],
            temperature=0.9,
            max_tokens=300,
        )

    async def generate_transition(
        self,
        dj: DJRecord,
        previous_song: dict | None,
        next_song: dict,
        babble_rate: float,
        max_seconds: int,
    ) -> str:
        """Generate DJ patter between two songs."""
        system = build_system_prompt(dj, babble_rate, max_seconds)

        if previous_song:
            user = (
                f"The song that just finished was \"{previous_song.get('title', 'Unknown')}\" "
                f"by {previous_song.get('artist', 'Unknown')}."
            )
            if previous_song.get("album"):
                user += f" From the album \"{previous_song['album']}\"."
            if previous_song.get("year"):
                user += f" Released in {previous_song['year']}."
        else:
            user = "You're about to play the first song of the set."

        user += (
            f"\n\nThe next song coming up is \"{next_song.get('title', 'Unknown')}\" "
            f"by {next_song.get('artist', 'Unknown')}."
        )
        if next_song.get("album"):
            user += f" From the album \"{next_song['album']}\"."
        if next_song.get("year"):
            user += f" Released in {next_song['year']}."

        user += "\n\nProvide a natural transition between these songs."

        return await self._llm.complete(
            messages=[LLMMessage("system", system), LLMMessage("user", user)],
            temperature=0.85,
            max_tokens=400,
        )

    def should_talk(self, talk_rate: float) -> bool:
        """Probabilistic check based on talk_rate."""
        return random.random() < talk_rate
