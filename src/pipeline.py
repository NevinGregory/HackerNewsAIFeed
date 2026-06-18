"""Pipeline orchestrator — ties together all stages of the HN → TikTok flow.

Each stage updates the story's status in the database, so interrupted runs
can be resumed from the last successful checkpoint.
"""

from __future__ import annotations

import logging

from src.composer import compose_video
from src.config import Config, get_config
from src.curator import curate
from src.db import HNStory, Database
from src.extractor import extract_article
from src.fetcher import fetch_top_stories
from src.script_writer import generate_script
from src.voice_generator import generate_voice

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# The full pipeline for one story
# ---------------------------------------------------------------------------


def process_story(
    story: HNStory,
    *,
    config: Config | None = None,
    db: Database | None = None,
) -> HNStory:
    """Run the complete pipeline on a single HN story.

    Each stage is gated — if a stage has already been completed (according
    to ``story.status``) it is skipped, making the pipeline resumable.

    Returns the updated story record with status ``rendered`` or ``failed``.
    """
    if config is None:
        config = get_config()

    cls = db  # short alias for readability

    try:
        # --- Stage 1: Extract article content -------------------------------
        if story.status in ("new",):
            logger.info("[%d] Extracting article → %s", story.hn_id, story.url)
            article = extract_article(story.url)
            result = curate(story, article)
            if not result.accepted:
                raise PipelineError(f"Curation rejected: {result.reason}")

            story.article_text = article.text  # type: ignore[union-attr]
            story.status = "extracted"
            if cls:
                cls.update_story(story)

        # --- Stage 2: Generate script --------------------------------------
        if story.status in ("new", "extracted"):
            logger.info("[%d] Generating script …", story.hn_id)
            script = generate_script(
                story.article_text,
                title=story.title,
                config=config,
            )
            if not script:
                raise PipelineError("Script generation produced empty output")
            story.script = script
            story.status = "scripted"
            if cls:
                cls.update_story(story)

        # --- Stage 3: Generate voiceover + word timestamps ---------------
        # ElevenLabs returns both audio and character-level timestamps
        # in one call — no separate STT step needed.
        if story.status in ("new", "extracted", "scripted"):
            logger.info("[%d] Generating voiceover …", story.hn_id)
            audio_path, word_data = generate_voice(story.script, config=config)
            if not audio_path or not word_data:
                raise PipelineError("Voice generation failed")
            story.audio_path = audio_path
            story.word_data = word_data
            story.status = "voiced"
            if cls:
                cls.update_story(story)

        # --- Stage 4: Compose video ----------------------------------------
        if story.status in ("new", "extracted", "scripted", "voiced"):
            logger.info("[%d] Composing video …", story.hn_id)
            video_path = compose_video(
                audio_path=story.audio_path,
                word_data=story.word_data,
                script=story.script,
                config=config,
            )
            if not video_path:
                raise PipelineError("Video composition failed")
            story.video_path = video_path
            story.status = "rendered"
            if cls:
                cls.update_story(story)

        logger.info("[%d] ✅ Done → %s", story.hn_id, story.video_path)

    except PipelineError as exc:
        logger.error("[%d] ❌ %s", story.hn_id, exc)
        # Remove from DB so it gets retried on the next run
        if cls:
            cls.delete_story(story)
    except Exception:
        logger.exception("[%d] Unexpected error", story.hn_id)
        if cls:
            cls.delete_story(story)

    return story


# ---------------------------------------------------------------------------
# Batch run
# ---------------------------------------------------------------------------


def run_pipeline(
    count: int = 5,
    *,
    config: Config | None = None,
) -> list[HNStory]:
    """Fetch HN top stories and run the full pipeline on up to *count* new ones.

    Stories already in the database (any status) are skipped — only truly
    new HN story IDs are processed.
    """
    if config is None:
        config = get_config()

    db = Database()

    # 1. Fetch current top stories
    stories_raw = fetch_top_stories(limit=count * 2)  # over-fetch
    logger.info("Fetched %d stories from HN API", len(stories_raw))

    # 2. Deduplicate against the database
    new_stories: list[HNStory] = []
    for raw in stories_raw:
        sid = raw["id"]
        if db.story_exists(sid):
            logger.debug("Story %d already in DB — skipping", sid)
            continue
        story = HNStory(
            hn_id=sid,
            title=raw.get("title", ""),
            url=raw.get("url", ""),
            score=raw.get("score", 0),
            author=raw.get("by", ""),
            hn_timestamp=raw.get("time", 0),
        )
        db.insert_story(story)
        new_stories.append(story)
        if len(new_stories) >= count:
            break

    logger.info("Processing %d new stories", len(new_stories))

    # 3. Process each
    results: list[HNStory] = []
    for story in new_stories:
        result = process_story(story, config=config, db=db)
        results.append(result)

    # 4. Summary
    rendered = sum(1 for r in results if r.status == "rendered")
    failed = sum(1 for r in results if r.status == "failed")
    logger.info("Batch complete: %d rendered, %d failed", rendered, failed)

    db.close()
    return results


def process_single_url(
    url: str,
    *,
    config: Config | None = None,
) -> HNStory | None:
    """Process a single article URL directly (bypassing HN fetching).

    Useful for testing or one-off video generation.
    """
    if config is None:
        config = get_config()

    # Create a synthetic story record
    story = HNStory(
        hn_id=hash(url) % 10_000_000,  # non-colliding pseudo-ID
        title="",
        url=url,
        score=0,
        author="manual",
        hn_timestamp=0,
    )

    return process_story(story, config=config)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class PipelineError(Exception):
    """Signals a non-fatal pipeline failure for a single story."""
