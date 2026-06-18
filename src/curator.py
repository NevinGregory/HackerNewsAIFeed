"""Story curator — decides which HN stories are worth turning into videos.

Not every HN front-page story makes good short-form content.  This module
filters out:
- Stories whose articles couldn't be extracted (paywalled, dead links)
- Stories that are too short (< 300 chars of extracted text)
- Non-English content (simple langdetect heuristic)
- Stories that are mostly code / lists / changelogs
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from src.db import HNStory
from src.extractor import ArticleContent

logger = logging.getLogger(__name__)

# Quick heuristic: if >50% of words contain non-ASCII chars common in
# CJK / Cyrillic / Arabic scripts, skip the story.
_NON_LATIN_RATIO_THRESHOLD = 0.5
_MIN_TEXT_LENGTH = 300


@dataclass
class CurationResult:
    """The outcome of curating a single story."""

    accepted: bool
    """``True`` if the story passed all filters."""

    reason: str = ""
    """Human-readable reason if rejected."""


def curate(story: HNStory, article: ArticleContent | None) -> CurationResult:
    """Apply all filters and return a decision.

    Args:
        story: The HN story record from the database.
        article: Extracted article content (or ``None`` if extraction failed).
    """
    # --- Filter 1: Extraction succeeded? ----------------------------------
    if article is None or not article.text.strip():
        return CurationResult(False, "No extractable article content")

    # --- Filter 2: Minimum text length ------------------------------------
    if len(article.text) < _MIN_TEXT_LENGTH:
        return CurationResult(
            False,
            f"Article too short ({len(article.text)} chars, need {_MIN_TEXT_LENGTH})",
        )

    # --- Filter 3: Skip obvious non-English --------------------------------
    if _is_non_english(article.text):
        return CurationResult(False, "Article appears to be non-English")

    # --- Filter 4: Skip code-heavy pages / changelogs ---------------------
    if _is_code_heavy(article.text):
        return CurationResult(False, "Article appears to be code-heavy or a changelog")

    # --- Filter 5: Skip "dead" HN stories ---------------------------------
    if story.score <= 0 and story.hn_timestamp > 0:
        return CurationResult(False, "Story has non-positive score")

    return CurationResult(True)


# ---------------------------------------------------------------------------
# Heuristics
# ---------------------------------------------------------------------------


def _is_non_english(text: str) -> bool:
    """Heuristic: count non-Latin words and compare to total words."""
    words = re.findall(r"\b\w+\b", text)
    if not words:
        return False

    non_latin = sum(
        1
        for w in words
        if any(ord(ch) > 0x024F for ch in w)  # beyond Latin-1 Supplement + Extended
    )
    return (non_latin / len(words)) > _NON_LATIN_RATIO_THRESHOLD


def _is_code_heavy(text: str) -> bool:
    """Return ``True`` if the text looks like code, a changelog, or a readme."""
    # Heuristic: high ratio of backtick-fenced blocks or lines starting with
    # bullet points, version numbers, etc.
    lines = text.splitlines()
    code_lines = 0

    for line in lines:
        stripped = line.strip()
        if stripped.startswith(("```", "~~~")):
            code_lines += 1
        elif re.match(r"^[vV]?\d+\.\d+\.\d+", stripped):
            code_lines += 1
        elif re.match(r"^[-*+]\s+`[^`]+`", stripped):
            code_lines += 1

    if len(lines) > 0 and (code_lines / len(lines)) > 0.3:
        return True

    # Also check for "changelog" / "release notes" signals
    first_100 = text[:500].lower()
    changelog_signals = [
        "changelog",
        "release notes",
        "what's new in",
        "version history",
    ]
    return any(signal in first_100 for signal in changelog_signals)
