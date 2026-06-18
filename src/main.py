"""CLI entry point for the HN → TikTok feed pipeline.

Usage::

    python -m src.main run --count 5
    python -m src.main run --url https://example.com/article
    python -m src.main list
"""

from __future__ import annotations

import logging
import sys

import click

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------


def _setup_logging(verbose: bool = False) -> None:
    """Configure coloured console logging."""
    level = logging.DEBUG if verbose else logging.INFO
    fmt = "%(asctime)s  %(levelname)-8s  %(message)s"
    datefmt = "%H:%M:%S"
    logging.basicConfig(level=level, format=fmt, datefmt=datefmt, stream=sys.stderr)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


@click.group()
@click.option("--verbose", "-v", is_flag=True, help="Enable debug logging.")
@click.pass_context
def cli(ctx: click.Context, verbose: bool) -> None:
    """HN AI Feed — convert Hacker News stories into TikTok-style videos."""
    _setup_logging(verbose)
    ctx.ensure_object(dict)


@cli.command("run")
@click.option(
    "--count",
    "-c",
    type=int,
    default=5,
    show_default=True,
    help="Number of top HN stories to process.",
)
@click.option(
    "--url",
    "-u",
    type=str,
    default=None,
    help="Process a single article URL (bypasses HN fetching).",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Fetch and extract, but don't generate scripts or videos.",
)
def run_cmd(count: int, url: str, dry_run: bool) -> None:
    """Fetch HN stories and generate videos."""
    from src.pipeline import run_pipeline, process_single_url

    if url:
        logger = logging.getLogger(__name__)
        logger.info("Processing single URL: %s", url)
        if dry_run:
            logger.info("Dry-run mode — extracting only")
            from src.extractor import extract_article

            article = extract_article(url)
            if article:
                click.echo(f"Title: {article.title}")
                click.echo(f"Words: {article.word_count}")
                click.echo(f"Preview: {article.text[:300]}...")
            else:
                click.echo("Extraction failed.")
            return
        result = process_single_url(url)
        if result and result.status == "rendered":
            click.echo(f"✅ Video: {result.video_path}")
        else:
            click.echo(f"❌ Failed: {result.error_message if result else 'Unknown'}")
    else:
        if dry_run:
            click.echo(f"Would process {count} stories (dry-run)")
            from src.fetcher import fetch_top_stories

            stories = fetch_top_stories(limit=count)
            for s in stories:
                click.echo(f"  [{s['id']}] {s['title']} → {s.get('url', '(no url)')}")
            return
        run_pipeline(count=count)


@cli.command("list")
@click.option("--status", "-s", type=str, default=None, help="Filter by status.")
@click.option("--limit", "-n", type=int, default=20, help="Max stories to show.")
def list_cmd(status: str | None, limit: int) -> None:
    """Show processed stories from the database."""
    from src.db import Database

    db = Database()
    if status:
        stories = db.get_stories_by_status(status, limit=limit)
    else:
        stories = db.get_all_stories(limit=limit)

    if not stories:
        click.echo("No stories found.")
        db.close()
        return

    click.echo(f"{'ID':<10} {'Status':<14} {'Score':<6} Title")
    click.echo("-" * 80)
    for s in stories:
        click.echo(
            f"{s.hn_id:<10} {s.status:<14} {s.score:<6} "
            f"{s.title[:55]}{'…' if len(s.title) > 55 else ''}"
        )
    db.close()


@cli.command("download-clip")
@click.argument("youtube_url")
def download_clip_cmd(youtube_url: str) -> None:
    """Download a YouTube video as a background clip for the composer."""
    from yt_dlp import YoutubeDL
    from src.config import get_config

    config = get_config()
    clips_dir = config.clips_dir
    clips_dir.mkdir(parents=True, exist_ok=True)
    existing = len(list(clips_dir.glob("*.mp4")))
    outtmpl = str(clips_dir / f"clip_{existing + 1}.mp4")

    click.echo(f"Downloading → {outtmpl}")
    options = {
        "format": "mp4[height<=1080]",
        "outtmpl": outtmpl,
        "merge_output_format": "mp4",
    }
    with YoutubeDL(options) as ytdl:
        ytdl.download([youtube_url])
    click.echo(f"✅ Downloaded: {outtmpl}")


@cli.command("reset")
def reset_cmd() -> None:
    """Wipe the database so all stories are re-processed on the next run."""
    from src.config import get_config

    config = get_config()
    db_path = config.cache_dir / "hn_feed.db"
    if db_path.exists():
        db_path.unlink()
        click.echo(f"✅ Deleted {db_path}")
    else:
        click.echo("No database found — nothing to reset.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    cli()
