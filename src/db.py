"""SQLite database for tracking processed Hacker News stories.

Each row represents one HN story that has entered the pipeline.  We persist
every intermediate artifact (extracted text, generated script, audio path,
word timestamps, and final video path) so that interrupted runs can resume
without re-processing.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.config import get_config


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class HNStory:
    """A Hacker News story as it flows through the pipeline."""

    hn_id: int
    """Hacker News item ID."""

    title: str
    """Story title."""

    url: str
    """Link to the article."""

    score: int
    """Upvote count at fetch time."""

    author: str
    """HN username of the submitter."""

    hn_timestamp: int
    """Unix timestamp from the HN API."""

    # -- Pipeline state --

    article_text: str = ""
    """Raw extracted article content."""

    script: str = ""
    """AI-generated short-form video script."""

    audio_path: str = ""
    """Path to the generated ``.mp3`` voiceover."""

    word_data_json: str = ""
    """JSON blob of Deepgram word-level timestamps."""

    video_path: str = ""
    """Path to the rendered ``.mp4`` output."""

    status: str = "new"
    """Pipeline status: ``new|extracted|scripted|voiced|transcribed|rendered|failed``."""

    error_message: str = ""
    """Last error encountered, if any."""

    created_at: str = ""
    updated_at: str = ""

    # -- Convenience accessors --

    @property
    def word_data(self) -> list[dict[str, Any]]:
        """Parse the stored JSON into Python objects."""
        if not self.word_data_json:
            return []
        return json.loads(self.word_data_json)

    @word_data.setter
    def word_data(self, value: list[dict[str, Any]]) -> None:
        self.word_data_json = json.dumps(value)

    @property
    def has_audio(self) -> bool:
        return bool(self.audio_path) and Path(self.audio_path).exists()

    @property
    def has_video(self) -> bool:
        return bool(self.video_path) and Path(self.video_path).exists()


# ---------------------------------------------------------------------------
# Database wrapper
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS stories (
    hn_id           INTEGER PRIMARY KEY,
    title           TEXT    NOT NULL,
    url             TEXT    NOT NULL DEFAULT '',
    score           INTEGER NOT NULL DEFAULT 0,
    author          TEXT    NOT NULL DEFAULT '',
    hn_timestamp    INTEGER NOT NULL DEFAULT 0,

    article_text    TEXT    NOT NULL DEFAULT '',
    script          TEXT    NOT NULL DEFAULT '',
    audio_path      TEXT    NOT NULL DEFAULT '',
    word_data_json  TEXT    NOT NULL DEFAULT '',
    video_path      TEXT    NOT NULL DEFAULT '',

    status          TEXT    NOT NULL DEFAULT 'new',
    error_message   TEXT    NOT NULL DEFAULT '',

    created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_stories_status ON stories(status);
"""


class Database:
    """Lightweight wrapper around a SQLite database for HN stories."""

    def __init__(self, db_path: Path | None = None):
        if db_path is None:
            db_path = get_config().cache_dir / "hn_feed.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)

        self._conn = sqlite3.connect(str(db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def story_exists(self, hn_id: int) -> bool:
        """Return ``True`` if this HN story is already in the database."""
        row = self._conn.execute(
            "SELECT 1 FROM stories WHERE hn_id = ?", (hn_id,)
        ).fetchone()
        return row is not None

    def get_story(self, hn_id: int) -> HNStory | None:
        """Retrieve a single story by HN ID, or ``None``."""
        row = self._conn.execute(
            "SELECT * FROM stories WHERE hn_id = ?", (hn_id,)
        ).fetchone()
        return _row_to_story(row) if row else None

    def get_stories_by_status(self, status: str, limit: int = 50) -> list[HNStory]:
        """Return stories with a given pipeline status."""
        rows = self._conn.execute(
            "SELECT * FROM stories WHERE status = ? ORDER BY hn_timestamp DESC LIMIT ?",
            (status, limit),
        ).fetchall()
        return [_row_to_story(r) for r in rows]

    def get_all_stories(self, limit: int = 100) -> list[HNStory]:
        """Return the most recent stories."""
        rows = self._conn.execute(
            "SELECT * FROM stories ORDER BY hn_timestamp DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [_row_to_story(r) for r in rows]

    def get_unprocessed_stories(self, limit: int = 100) -> list[HNStory]:
        """Return stories that haven't been fully rendered yet."""
        rows = self._conn.execute(
            "SELECT * FROM stories WHERE status != 'rendered' AND status != 'failed' "
            "ORDER BY score DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [_row_to_story(r) for r in rows]

    def insert_story(self, story: HNStory) -> None:
        """Insert a new story (or ignore if it already exists)."""
        now = datetime.now(timezone.utc).isoformat()
        story.created_at = now
        story.updated_at = now
        self._conn.execute(
            """INSERT OR IGNORE INTO stories
               (hn_id, title, url, score, author, hn_timestamp,
                article_text, script, audio_path, word_data_json, video_path,
                status, error_message, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            _story_to_tuple(story),
        )
        self._conn.commit()

    def update_story(self, story: HNStory) -> None:
        """Persist an existing story's current state."""
        story.updated_at = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            """UPDATE stories SET
               title=?, url=?, score=?, author=?, hn_timestamp=?,
               article_text=?, script=?, audio_path=?, word_data_json=?, video_path=?,
               status=?, error_message=?, updated_at=?
               WHERE hn_id=?""",
            [*_story_to_tuple(story)[1:-2], story.updated_at, story.hn_id],
        )
        self._conn.commit()

    def mark_failed(self, story: HNStory, error: str) -> None:
        """Mark a story as failed with an error message."""
        story.status = "failed"
        story.error_message = error
        self.update_story(story)

    def delete_story(self, story: HNStory) -> None:
        """Remove a story from the database so it can be retried."""
        self._conn.execute("DELETE FROM stories WHERE hn_id = ?", (story.hn_id,))
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _row_to_story(row: sqlite3.Row) -> HNStory:
    """Convert a database row into an :class:`HNStory` instance."""
    return HNStory(
        hn_id=row["hn_id"],
        title=row["title"],
        url=row["url"],
        score=row["score"],
        author=row["author"],
        hn_timestamp=row["hn_timestamp"],
        article_text=row["article_text"],
        script=row["script"],
        audio_path=row["audio_path"],
        word_data_json=row["word_data_json"],
        video_path=row["video_path"],
        status=row["status"],
        error_message=row["error_message"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _story_to_tuple(story: HNStory) -> tuple:
    """Serialize a story into a tuple for SQL parameter binding."""
    return (
        story.hn_id,
        story.title,
        story.url,
        story.score,
        story.author,
        story.hn_timestamp,
        story.article_text,
        story.script,
        story.audio_path,
        story.word_data_json,
        story.video_path,
        story.status,
        story.error_message,
        story.created_at,
        story.updated_at,
    )
