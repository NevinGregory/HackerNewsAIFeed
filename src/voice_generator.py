"""Voice generation via ElevenLabs TTS (with word-level timestamps).

Converts a text script into a spoken ``.mp3`` file **and** word-level
timing data in a single API call using ElevenLabs'
``convert_with_timestamps`` endpoint.

Because ElevenLabs returns character-level timestamps for the *exact*
input text, caption words are guaranteed to match the script — no STT
transcription errors, no alignment needed.

Audio and word data are cached together so repeated runs against the
same script are instant.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
from pathlib import Path
from typing import Any

from elevenlabs.client import ElevenLabs

from src.config import Config, get_config

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def generate_voice(
    script: str,
    *,
    config: Config | None = None,
) -> tuple[str | None, list[dict[str, Any]]]:
    """Synthesise *script* as speech and return ``(audio_path, word_data)``.

    Returns ``(None, [])`` on failure.
    """
    if config is None:
        config = get_config()

    audio_path = _audio_cache_path(script, config)
    words_path = _words_cache_path(audio_path)

    # --- Cache hit --------------------------------------------------------
    if audio_path.exists() and words_path.exists():
        logger.info("Voiceover already cached → %s", audio_path.name)
        word_data = json.loads(words_path.read_text(encoding="utf-8"))
        return str(audio_path), word_data

    # --- Generate ----------------------------------------------------------
    logger.info(
        "Generating voiceover with ElevenLabs (voice=%s, model=%s)",
        config.elevenlabs_voice_id,
        config.elevenlabs_voice_model,
    )

    try:
        client = ElevenLabs(api_key=config.elevenlabs_api_key)

        response = client.text_to_speech.convert_with_timestamps(
            text=script,
            voice_id=config.elevenlabs_voice_id,
            model_id=config.elevenlabs_voice_model,
            output_format="mp3_44100_128",
            apply_text_normalization="off",
        )
    except Exception as exc:
        logger.error("ElevenLabs TTS request failed: %s", exc)
        return None, []

    # --- Extract audio bytes -----------------------------------------------
    audio_bytes = _extract_audio(response)
    if not audio_bytes:
        logger.error("ElevenLabs returned no audio")
        return None, []

    # --- Extract word data from character timestamps -----------------------
    alignment = _extract_alignment(response)
    word_data = _chars_to_words(alignment) if alignment else _fallback_word_data(script)

    # --- Cache -------------------------------------------------------------
    audio_path.write_bytes(audio_bytes)
    words_path.write_text(json.dumps(word_data), encoding="utf-8")
    logger.info(
        "Voiceover saved → %s (%d words, %d KB)",
        audio_path.name,
        len(word_data),
        len(audio_bytes) // 1024,
    )

    return str(audio_path), word_data


# ---------------------------------------------------------------------------
# Character → word conversion
# ---------------------------------------------------------------------------


def _chars_to_words(
    alignment: Any,
) -> list[dict[str, Any]]:
    """Convert ElevenLabs character-level timestamps to word-level.

    Uses spaces in the character list as word boundaries.  Each word
    entry gets the start time of its first character and the end time of
    its last character.
    """
    # Duck-type: alignment may be a Pydantic model or plain dict
    chars: list[str] = getattr(alignment, "characters", None) or alignment.get(
        "characters", []
    )
    starts: list[float] = getattr(
        alignment, "character_start_times_seconds", None
    ) or alignment.get("character_start_times_seconds", [])
    ends: list[float] = getattr(
        alignment, "character_end_times_seconds", None
    ) or alignment.get("character_end_times_seconds", [])

    if not chars:
        return []

    words: list[dict[str, Any]] = []
    current_chars: list[str] = []
    word_start: float | None = None

    for i, ch in enumerate(chars):
        if ch.isspace():
            if current_chars and word_start is not None:
                words.append(
                    {
                        "word": "".join(current_chars),
                        "punctuated_word": "".join(current_chars),
                        "start": word_start,
                        "end": ends[i - 1],
                    }
                )
                current_chars = []
                word_start = None
        else:
            if word_start is None:
                word_start = starts[i]
            current_chars.append(ch)

    # Final word (no trailing space)
    if current_chars and word_start is not None:
        words.append(
            {
                "word": "".join(current_chars),
                "punctuated_word": "".join(current_chars),
                "start": word_start,
                "end": ends[-1] if ends else 0.0,
            }
        )

    return words


def _fallback_word_data(script: str) -> list[dict[str, Any]]:
    """Generate evenly-spaced word data when alignment is unavailable.

    Used as a last-resort fallback so the video can still render even
    without precise timestamps (highlighting will be approximate).
    """
    words = script.split()
    if not words:
        return []

    # Assume ~3 words/second for a rough estimate
    words_per_second = 3.0
    duration = len(words) / words_per_second

    result: list[dict[str, Any]] = []
    for i, word in enumerate(words):
        start = (i / len(words)) * duration
        end = ((i + 1) / len(words)) * duration
        result.append(
            {
                "word": word,
                "punctuated_word": word,
                "start": round(start, 3),
                "end": round(end, 3),
            }
        )
    return result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_audio(response: Any) -> bytes:
    """Pull raw audio bytes from the ElevenLabs SDK response.

    ``AudioWithTimestampsResponse.audio_base_64`` is a base64-encoded
    string that we decode back to bytes.
    """
    b64: str | None = getattr(response, "audio_base_64", None)
    if b64:
        return base64.b64decode(b64)
    return b""


def _extract_alignment(response: Any) -> Any | None:
    """Pull the alignment model from the ElevenLabs SDK response."""
    return getattr(response, "alignment", None)


def _audio_cache_path(script: str, config: Config) -> Path:
    """Build a deterministic cache path from the script + voice identity."""
    script_hash = hashlib.sha256(script.encode()).hexdigest()
    voice_hash = hashlib.sha256(
        f"{config.elevenlabs_voice_id}-{config.elevenlabs_voice_model}".encode()
    ).hexdigest()
    filename = f"audio_{script_hash[:12]}-{voice_hash[:12]}.mp3"
    return config.cache_dir / filename


def _words_cache_path(audio_path: Path) -> Path:
    return audio_path.with_suffix(".words.json")
