"""Hacker News API fetcher.

Talks to the official `Firebase-backed REST API
<https://github.com/HackerNews/API>`_ which requires no authentication.

Typical usage::

    from src.fetcher import fetch_top_stories

    stories = fetch_top_stories(limit=5)
    for s in stories:
        print(s["title"], s.get("url"))
"""

from __future__ import annotations

import logging
from typing import Any

import requests

from src.config import get_config

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def fetch_top_stories(limit: int = 10) -> list[dict[str, Any]]:
    """Return the top *limit* stories currently on the HN front page.

    Each dictionary contains the full HN item payload:
    ``id``, ``title``, ``url``, ``score``, ``by``, ``descendants``,
    ``time``, ``type``, and optionally ``text`` (for Ask HN / Show HN).

    Stories without a ``url`` (Ask HN, jobs, polls) are excluded.
    """
    config = get_config()

    # 1. Fetch the list of top story IDs -----------------------------------
    logger.info("Fetching top story IDs from %s", config.hn_top_stories_url)
    resp = requests.get(config.hn_top_stories_url, timeout=15)
    resp.raise_for_status()
    all_ids: list[int] = resp.json()

    # 2. Fetch individual story details in parallel-ish --------------------
    stories: list[dict[str, Any]] = []
    target_ids = all_ids[: limit * 2]  # over-fetch to account for no-URL items

    for story_id in target_ids:
        if len(stories) >= limit:
            break

        try:
            story = _fetch_item(story_id, config)
        except requests.RequestException:
            logger.warning("Failed to fetch story %d — skipping", story_id)
            continue

        if story is None:
            continue

        # Skip items without a link (Ask HN, jobs, polls)
        if not story.get("url"):
            logger.debug(
                "Skipping story %d (%s) — no URL", story_id, story.get("title", "")
            )
            continue

        stories.append(story)

    logger.info("Fetched %d stories with URLs", len(stories))
    return stories[:limit]


def fetch_story_by_id(story_id: int) -> dict[str, Any] | None:
    """Fetch a single HN story by its numeric ID."""
    config = get_config()
    return _fetch_item(story_id, config)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _fetch_item(item_id: int, config: Any = None) -> dict[str, Any] | None:
    """Fetch a single HN item.  Returns ``None`` if the item doesn't exist."""
    if config is None:
        config = get_config()
    url = config.hn_item_url.format(id=item_id)
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    if data is None:
        logger.debug("HN item %d returned null", item_id)
        return None
    return data
