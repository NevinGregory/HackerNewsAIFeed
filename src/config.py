"""Application configuration loaded from environment variables.

All settings are read from a ``.env`` file at the project root.  A template
is provided as ``.env.example`` — copy it to ``.env`` and fill in your keys.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Locate the project root (one level above src/)
# ---------------------------------------------------------------------------
PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent

# Load .env once at import time
_load_path = PROJECT_ROOT / ".env"
if _load_path.exists():
    load_dotenv(_load_path)


# ---------------------------------------------------------------------------
# Helper — read env var or raise a clear error
# ---------------------------------------------------------------------------


def _required(key: str) -> str:
    """Return *key* from the environment or raise a helpful message."""
    value = os.getenv(key)
    if value is None or value.strip() == "":
        raise RuntimeError(
            f"Missing required environment variable: {key}\n"
            f"Copy .env.example to .env and fill in your API keys."
        )
    return value


def _optional(key: str, default: str = "") -> str:
    """Return *key* from the environment or *default*."""
    return os.getenv(key, default)


# ---------------------------------------------------------------------------
# Configuration dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Config:
    """All configuration values for the HN → TikTok pipeline."""

    # -- API keys -----------------------------------------------------------

    elevenlabs_api_key: str
    """ElevenLabs API key for TTS (required)."""

    gemini_api_key: str
    """Google Gemini API key for script generation (optional if using OpenAI)."""

    openai_api_key: str
    """OpenAI API key for script generation (optional if using Gemini)."""

    deepgram_api_key: str
    """Deepgram API key for legacy TTS + STT (optional — kept for fallback)."""

    # -- Script generation --------------------------------------------------

    script_provider: str
    """Which LLM to use for script writing: ``"gemini"`` or ``"openai"``."""

    gemini_model: str
    """Gemini model name (e.g. ``gemini-2.0-flash``)."""

    openai_model: str
    """OpenAI model name (e.g. ``gpt-4o-mini``)."""

    # -- ElevenLabs settings ------------------------------------------------

    elevenlabs_voice_id: str
    """ElevenLabs voice ID (e.g. ``21m00Tcm4TlvDq8ikWAM`` for Rachel)."""

    elevenlabs_voice_model: str
    """ElevenLabs voice model (e.g. ``eleven_turbo_v2_5``)."""

    # -- HN API -------------------------------------------------------------

    hn_top_stories_url: str
    hn_item_url: str

    # -- Paths --------------------------------------------------------------

    output_dir: Path
    """Directory where final ``.mp4`` videos are written."""

    cache_dir: Path
    """Directory for generated ``.mp3`` audio files."""

    assets_dir: Path
    """Directory containing ``fonts/`` and ``clips/`` subdirectories."""

    # ------------------------------------------------------------------
    # Derived helpers
    # ------------------------------------------------------------------

    @property
    def fonts_dir(self) -> Path:
        return self.assets_dir / "fonts"

    @property
    def clips_dir(self) -> Path:
        return self.assets_dir / "clips"


# ---------------------------------------------------------------------------
# Singleton instance
# ---------------------------------------------------------------------------

_config: Config | None = None


def get_config() -> Config:
    """Return the (cached) :class:`Config` singleton."""
    global _config
    if _config is None:
        _config = Config(
            deepgram_api_key=_optional("DEEPGRAM_API_KEY"),
            elevenlabs_api_key=_required("ELEVENLABS_API_KEY"),
            gemini_api_key=_optional("GEMINI_API_KEY"),
            openai_api_key=_optional("OPENAI_API_KEY"),
            script_provider=_optional("SCRIPT_PROVIDER", "gemini").lower(),
            gemini_model=_optional("GEMINI_MODEL", "gemini-3.5-flash"),
            openai_model=_optional("OPENAI_MODEL", "gpt-4o-mini"),
            elevenlabs_voice_id=_optional(
                "ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM"
            ),
            elevenlabs_voice_model=_optional(
                "ELEVENLABS_VOICE_MODEL", "eleven_turbo_v2_5"
            ),
            hn_top_stories_url=_optional(
                "HN_TOP_STORIES_URL",
                "https://hacker-news.firebaseio.com/v0/topstories.json",
            ),
            hn_item_url=_optional(
                "HN_ITEM_URL",
                "https://hacker-news.firebaseio.com/v0/item/{id}.json",
            ),
            output_dir=PROJECT_ROOT / _optional("OUTPUT_DIR", "videos"),
            cache_dir=PROJECT_ROOT / _optional("CACHE_DIR", "cache"),
            assets_dir=PROJECT_ROOT / _optional("ASSETS_DIR", "assets"),
        )
        # Ensure output directories exist
        _config.output_dir.mkdir(parents=True, exist_ok=True)
        _config.cache_dir.mkdir(parents=True, exist_ok=True)
        _config.fonts_dir.mkdir(parents=True, exist_ok=True)
        _config.clips_dir.mkdir(parents=True, exist_ok=True)
    return _config
