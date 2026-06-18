"""AI script writer — turns HN articles into punchy short-form video scripts.

Uses Google Gemini or OpenAI to convert a long-form tech article into a
30–60 second script optimized for the TikTok/Reels format.  Includes
retry logic for rate-limit errors and optional fallback between providers.
"""

from __future__ import annotations

import logging
import time

from src.config import Config, get_config

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Prompt template
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You are a TikTok tech explainer. Convert the article below into a 30-60
second vertical-video script. Follow these rules strictly:

1. HOOK: Start with one provocative question or surprising fact.
2. BODY: 3-5 punchy sentences that explain the key point. Use short,
   conversational words. No jargon.
4. Keep the total script between 80 and 200 words.
5. Output ONLY the script text. No labels, no markdown, no intro phrases.

Example format:
Did you know your smartwatch can detect a heart attack before it happens?
Researchers just trained an AI model on 10 million heartbeats. And it
spotted arrhythmias that doctors missed. The wild part? It runs entirely
on-device with no internet needed. This could save thousands of lives every year.
"""

_USER_MESSAGE_TEMPLATE = """\
Article title: {title}

Article content:
{content}
"""

_MAX_ARTICLE_CHARS = 3000
_RETRY_MAX_ATTEMPTS = 4
_RETRY_BASE_DELAY = 5.0  # seconds — doubles each attempt


# ---------------------------------------------------------------------------
# Gemini provider (google.genai SDK)
# ---------------------------------------------------------------------------


def _generate_with_gemini(
    config: Config,
    system_prompt: str,
    user_content: str,
) -> str:
    """Generate a script using Google Gemini with retry on rate limits."""
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=config.gemini_api_key)

    for attempt in range(1, _RETRY_MAX_ATTEMPTS + 1):
        try:
            response = client.models.generate_content(
                model=config.gemini_model,
                contents=user_content,
                config=types.GenerateContentConfig(
                    system_instruction=system_prompt,
                ),
            )
            return (response.text or "").strip()

        except Exception as exc:
            msg = str(exc).lower()
            is_rate_limit = (
                "429" in str(exc)
                or "quota" in msg
                or "rate" in msg
                or "resource_exhausted" in msg
            )

            if is_rate_limit and attempt < _RETRY_MAX_ATTEMPTS:
                delay = _RETRY_BASE_DELAY * (2 ** (attempt - 1))
                logger.warning(
                    "Gemini rate-limited (attempt %d/%d) — retrying in %.0fs …",
                    attempt,
                    _RETRY_MAX_ATTEMPTS,
                    delay,
                )
                time.sleep(delay)
                continue

            # Non-retryable error or exhausted attempts
            logger.error("Gemini generation failed (attempt %d): %s", attempt, exc)
            break

    return ""


# ---------------------------------------------------------------------------
# OpenAI provider
# ---------------------------------------------------------------------------


def _generate_with_openai(
    config: Config,
    system_prompt: str,
    user_content: str,
) -> str:
    """Generate a script using OpenAI's chat completions API with retry."""
    from openai import OpenAI

    client = OpenAI(api_key=config.openai_api_key)

    for attempt in range(1, _RETRY_MAX_ATTEMPTS + 1):
        try:
            completion = client.chat.completions.create(
                model=config.openai_model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content},
                ],
                temperature=0.8,
            )
            content = completion.choices[0].message.content
            return (content or "").strip()

        except Exception as exc:
            msg = str(exc).lower()
            is_rate_limit = (
                "429" in str(exc)
                or "rate" in msg
                or "quota" in msg
                or "capacity" in msg
            )

            if is_rate_limit and attempt < _RETRY_MAX_ATTEMPTS:
                delay = _RETRY_BASE_DELAY * (2 ** (attempt - 1))
                logger.warning(
                    "OpenAI rate-limited (attempt %d/%d) — retrying in %.0fs …",
                    attempt,
                    _RETRY_MAX_ATTEMPTS,
                    delay,
                )
                time.sleep(delay)
                continue

            logger.error("OpenAI generation failed (attempt %d): %s", attempt, exc)
            break

    return ""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def generate_script(
    article_text: str,
    title: str = "",
    *,
    config: Config | None = None,
) -> str:
    """Convert *article_text* into a 30–60 second TikTok-style script.

    Supports ``gemini`` (default) and ``openai`` providers.  If both API
    keys are configured, a Gemini failure automatically falls back to
    OpenAI.
    """
    if config is None:
        config = get_config()

    # Truncate overly long articles
    if len(article_text) > _MAX_ARTICLE_CHARS:
        logger.info(
            "Truncating article from %d to %d chars",
            len(article_text),
            _MAX_ARTICLE_CHARS,
        )
        article_text = article_text[:_MAX_ARTICLE_CHARS]

    user_content = _USER_MESSAGE_TEMPLATE.format(
        title=title or "Tech Article",
        content=article_text,
    )

    # --- Attempt primary provider ------------------------------------------
    primary = config.script_provider
    fallback = "openai" if primary == "gemini" else "gemini"

    script = _try_provider(primary, config, user_content)

    # --- Fallback if primary failed and fallback key is available ----------
    if not script and _provider_has_key(fallback, config):
        logger.info("Primary (%s) failed — falling back to %s", primary, fallback)
        script = _try_provider(fallback, config, user_content)

    # --- Validate ----------------------------------------------------------
    if not script:
        return ""

    word_count = len(script.split())
    logger.info("Generated script — %d words", word_count)

    if word_count < 20:
        logger.warning("Script too short (%d words) — discarding", word_count)
        return ""

    return script


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _try_provider(provider: str, config: Config, user_content: str) -> str:
    """Call the right provider function, guarding for missing API keys."""
    if provider == "openai":
        if not config.openai_api_key:
            logger.warning("OpenAI key not set — skipping")
            return ""
        try:
            return _generate_with_openai(config, _SYSTEM_PROMPT, user_content)
        except Exception as exc:
            logger.error("OpenAI failed: %s", exc)
            return ""
    else:
        if not config.gemini_api_key:
            logger.warning("Gemini key not set — skipping")
            return ""
        try:
            return _generate_with_gemini(config, _SYSTEM_PROMPT, user_content)
        except Exception as exc:
            logger.error("Gemini failed: %s", exc)
            return ""


def _provider_has_key(provider: str, config: Config) -> bool:
    """Return ``True`` if an API key is configured for *provider*."""
    if provider == "openai":
        return bool(config.openai_api_key)
    return bool(config.gemini_api_key)
