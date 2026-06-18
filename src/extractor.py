"""Article content extraction via ``trafilatura``.

Downloads the page behind a URL and extracts the main article text (as
clean Markdown) together with metadata like the title, author, and any
hero image URL.

If ``trafilatura`` fails to extract useful content we fall back to a
bare-bones ``requests`` + ``BeautifulSoup`` meta-description scrape.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import requests
from trafilatura import extract, fetch_url
from trafilatura.metadata import extract_metadata

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class ArticleContent:
    """Extracted article content and metadata."""

    title: str
    """Article title (from ``<title>`` or Open Graph)."""

    text: str
    """Main body text in Markdown format."""

    url: str
    """The canonical URL that was fetched."""

    author: str = ""
    """Article author, if detectable."""

    site_name: str = ""
    """Name of the publishing site (e.g. ``The Verge``)."""

    image_url: str = ""
    """URL of the hero / Open Graph image."""

    description: str = ""
    """Meta description or short summary."""

    @property
    def word_count(self) -> int:
        """Approximate word count of the extracted text."""
        return len(self.text.split()) if self.text else 0


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def extract_article(url: str, timeout: int = 15) -> ArticleContent | None:
    """Extract article content from *url*.

    Returns ``None`` if the URL is unreachable or no meaningful text
    could be extracted.
    """
    logger.info("Extracting content from %s", url)

    # --- Step 1: Download page --------------------------------------------
    downloaded = fetch_url(url)
    if downloaded is None:
        logger.warning("trafilatura could not download %s", url)
        return _fallback_extract(url, timeout)

    # --- Step 2: Extract metadata -----------------------------------------
    metadata: dict[str, Any] = {}
    try:
        raw_metadata = extract_metadata(downloaded, default_url=url)
        if raw_metadata is not None:
            metadata = raw_metadata.as_dict()
    except Exception:
        logger.debug("Metadata extraction failed for %s", url)

    # --- Step 3: Extract main text ----------------------------------------
    raw_text = extract(downloaded, output_format="markdown", with_metadata=True)

    if not raw_text or len(raw_text.strip()) < 100:
        logger.warning("trafilatura extracted insufficient text from %s", url)
        return _fallback_extract(url, timeout)

    # --- Step 4: Build result ---------------------------------------------
    return ArticleContent(
        title=metadata.get("title") or _extract_title_from_text(raw_text),
        text=raw_text.strip(),
        url=url,
        author=metadata.get("author") or "",
        site_name=metadata.get("sitename") or "",
        image_url=metadata.get("image") or "",
        description=metadata.get("description") or "",
    )


# ---------------------------------------------------------------------------
# Fallback: basic requests + BeautifulSoup scrape
# ---------------------------------------------------------------------------


def _fallback_extract(url: str, timeout: int) -> ArticleContent | None:
    """Minimal extraction when trafilatura fails (paywalls, JS-only pages)."""
    try:
        resp = requests.get(
            url,
            timeout=timeout,
            headers={"User-Agent": "Mozilla/5.0 (compatible; HNFeedBot/0.1)"},
        )
        resp.raise_for_status()
    except requests.RequestException:
        logger.warning("Fallback extraction also failed for %s", url)
        return None

    from bs4 import BeautifulSoup

    soup = BeautifulSoup(resp.text, "html.parser")

    # Title
    title = ""
    og_title = soup.find("meta", property="og:title")
    if og_title is not None:
        content = og_title.get("content")
        if isinstance(content, str):
            title = content
    if not title and soup.title:
        title = soup.title.get_text(strip=True)

    # Description
    description = ""
    og_desc = soup.find("meta", property="og:description")
    if og_desc is not None:
        content = og_desc.get("content")
        if isinstance(content, str):
            description = content
    if not description:
        meta_desc = soup.find("meta", attrs={"name": "description"})
        if meta_desc is not None:
            content = meta_desc.get("content", "")
            if isinstance(content, str):
                description = content

    # Image
    image_url = ""
    og_image = soup.find("meta", property="og:image")
    if og_image is not None:
        content = og_image.get("content")
        if isinstance(content, str):
            image_url = content

    # Body text: grab all <p> tags
    paragraphs = [
        p.get_text(strip=True)
        for p in soup.find_all("p")
        if len(p.get_text(strip=True)) > 40
    ]
    text = "\n\n".join(paragraphs)

    if len(text) < 100:
        logger.warning("Fallback also produced insufficient text for %s", url)
        return None

    return ArticleContent(
        title=title,
        text=text,
        url=url,
        description=description,
        image_url=image_url,
    )


def _extract_title_from_text(markdown_text: str) -> str:
    """Try to grab a title from the first line of Markdown."""
    for line in markdown_text.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith(("#", "!", "[")):
            return stripped[:200]
    return "Untitled"
