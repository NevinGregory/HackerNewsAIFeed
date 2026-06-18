"""Audio transcription via Deepgram STT (Nova-2 REST API).

Transcribes the voiceover back through Deepgram's Nova-2 speech-to-text
endpoint to obtain **word-level timestamps** for caption highlighting.

The Deepgram listen endpoint returns a JSON response with word-level data
in: ``results.channels[0].alternatives[0].words`` — each word dict contains
``word``, ``start``, ``end``, ``punctuated_word``, and ``confidence``.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import requests

from src.config import Config, get_config

logger = logging.getLogger(__name__)

DEEPGRAM_LISTEN_URL = "https://api.deepgram.com/v1/listen"


def transcribe_audio(
    audio_path: str,
    *,
    config: Config | None = None,
) -> list[dict[str, Any]]:
    """Transcribe *audio_path* and return word-level timing data.

    Returns an empty list on failure.
    """
    if config is None:
        config = get_config()

    filepath = Path(audio_path)
    if not filepath.exists():
        logger.error("Audio file not found: %s", audio_path)
        return []

    logger.info(
        "Transcribing %s (%.1f KB)", filepath.name, filepath.stat().st_size / 1024
    )

    headers: dict[str, str | bytes] = {
        "Authorization": f"Token {config.deepgram_api_key}",
        "Content-Type": "audio/mpeg",
    }
    params = {
        "model": "nova-2",
        "smart_format": "true",
        "punctuate": "true",
    }

    try:
        with open(filepath, "rb") as fh:
            resp = requests.post(
                DEEPGRAM_LISTEN_URL,
                data=fh,
                params=params,
                headers=headers,
                timeout=60,
            )
        resp.raise_for_status()
    except requests.RequestException as exc:
        logger.error("Deepgram STT request failed: %s", exc)
        return []

    data: dict[str, Any] = resp.json()
    try:
        words: list[dict[str, Any]] = data["results"]["channels"][0]["alternatives"][0][
            "words"
        ]
    except (KeyError, IndexError, TypeError) as exc:
        logger.error("Unexpected Deepgram response structure: %s", exc)
        # Log a snippet for debugging
        logger.debug(
            "Response keys: %s",
            list(data.keys()) if isinstance(data, dict) else type(data),
        )
        return []

    logger.info(
        "Transcribed %d words (%.1f s total)",
        len(words),
        words[-1]["end"] if words else 0,
    )
    return words
