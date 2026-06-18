"""Video composer — assembles the final Minecraft-parkour TikTok video.

Adapted from ``sarrtle/vidgen``'s ``RenderStory.render_three_words()``.
Shows 3 words at a time with the currently-spoken word highlighted in
colour — the classic TikTok caption aesthetic.
"""

from __future__ import annotations

import difflib
import logging
import random
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from moviepy import AudioFileClip, CompositeVideoClip, TextClip, VideoFileClip
from PIL import ImageFont

from src.config import Config, get_config

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VIDEO_WIDTH = 1080
VIDEO_HEIGHT = 1920
FPS = 30
FONT_SIZE = 62
TEXT_COLOR = "white"
HIGHLIGHT_COLOR = "#0000ff"
STROKE_COLOR = "black"
STROKE_WIDTH = 4
TEXT_MARGIN = (0, 0, 10, 30)  # (left, right, top, bottom) padding to prevent clipping
WORDS_PER_GROUP = 3
CAPTION_Y = 960  # vertical centre of caption line (middle of 1080×1920)

# Fixed pixel offset from CAPTION_Y for the caption text position (top-left).
# Uses the same offset for both single-line and overflow cases so every word
# sits at the same vertical position regardless of glyph ascenders/descenders.
CAPTION_Y_OFFSET = 40


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def compose_video(
    audio_path: str,
    word_data: list[dict[str, Any]],
    *,
    config: Config | None = None,
    output_path: str | Path | None = None,
    background_path: str | Path | None = None,
    font_path: str | Path | None = None,
    highlight_color: str = HIGHLIGHT_COLOR,
    script: str | None = None,
) -> str | None:
    """Create a vertical short-form video from audio + word timestamps.

    When *script* is provided, word_data is aligned against the original
    script text so captions use the author's exact words instead of STT
    guesses (which often mangle proper nouns like "OpenAI" → "OpenEye").
    """
    if config is None:
        config = get_config()

    if not word_data:
        logger.error("No word data")
        return None

    audio_file = Path(audio_path)
    if not audio_file.exists():
        logger.error("Audio file not found: %s", audio_path)
        return None

    bg_path = _resolve_background(background_path, config)
    if bg_path is None:
        return None

    font_pil = _resolve_font(font_path, config)
    if font_pil is None:
        return None
    font_str = str(font_pil.path)

    if output_path is None:
        output_path = _make_output_path(config)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # --- Load assets ------------------------------------------------------
    logger.info("Loading background: %s", bg_path.name)
    bg_clip = VideoFileClip(str(bg_path))
    audio_clip = AudioFileClip(str(audio_file))
    audio_duration = audio_clip.duration
    logger.info("Audio duration: %.1f s", audio_duration)

    # --- Trim background --------------------------------------------------
    if bg_clip.duration < audio_duration:
        bg_clip = bg_clip.with_duration(audio_duration)
    else:
        max_start = bg_clip.duration - audio_duration
        start = random.uniform(0, max_start) if max_start > 0 else 0.0
        bg_clip = bg_clip.subclipped(start, start + audio_duration)

    # --- Scale background to fill vertical 9:16, crop sides -----------
    # e.g. 2560×1440 → scale height to 1920 → 3413×1920 → crop to 1080×1920
    orig_w, orig_h = bg_clip.w, bg_clip.h
    scale = VIDEO_HEIGHT / orig_h
    new_w = int(orig_w * scale)
    bg_clip = bg_clip.resized(new_size=(new_w, VIDEO_HEIGHT))
    crop_x = (new_w - VIDEO_WIDTH) // 2
    bg_clip = bg_clip.cropped(x1=crop_x, y1=0, x2=crop_x + VIDEO_WIDTH, y2=VIDEO_HEIGHT)
    logger.info(
        "Background scaled %d×%d → %d×%d, cropped to %d×%d",
        orig_w,
        orig_h,
        new_w,
        VIDEO_HEIGHT,
        VIDEO_WIDTH,
        VIDEO_HEIGHT,
    )

    # --- Align captions to original script --------------------------------
    if script:
        word_data = _align_script_to_stt(script, word_data)
        logger.info("Aligned %d caption words from original script", len(word_data))

    # --- Build word groups (3 words at a time) ----------------------------
    groups = _build_word_groups(word_data, WORDS_PER_GROUP)

    # --- Create text clips ------------------------------------------------
    base_clips: list[TextClip] = []
    highlight_clips: list[TextClip] = []

    # Measure space width once
    space_width = font_pil.getlength(" ")

    for group in groups:
        _create_group_clips(
            group=group,
            base_clips=base_clips,
            highlight_clips=highlight_clips,
            font_str=font_str,
            font_obj=font_pil,
            space_width=space_width,
            center_x=VIDEO_WIDTH / 2,
            y_pos=CAPTION_Y,
            highlight_color=highlight_color,
        )

    # --- Composite (text on TOP of background) ----------------------------
    all_clips: list = [bg_clip] + base_clips + highlight_clips
    final = CompositeVideoClip(all_clips, size=(VIDEO_WIDTH, VIDEO_HEIGHT))
    final = final.with_audio(audio_clip)
    final = final.with_duration(audio_duration)

    # --- Render ------------------------------------------------------------
    logger.info("Rendering %s …", output_path.name)
    final.write_videofile(
        str(output_path),
        fps=FPS,
        codec="libx264",
        audio_codec="aac",
        preset="medium",
        threads=4,
        logger=None,
    )

    bg_clip.close()
    audio_clip.close()
    final.close()

    logger.info("Video saved → %s", output_path)
    return str(output_path)


# ---------------------------------------------------------------------------
# Word grouping
# ---------------------------------------------------------------------------


def _build_word_groups(
    word_data: list[dict[str, Any]],
    group_size: int,
) -> list[dict[str, Any]]:
    """Group words into chunks of *group_size* for the caption effect."""
    groups: list[dict[str, Any]] = []
    for i in range(0, len(word_data), group_size):
        chunk = word_data[i : i + group_size]
        groups.append(
            {
                "words": [w.get("punctuated_word", w.get("word", "")) for w in chunk],
                "start_time": chunk[0]["start"],
                "end_time": chunk[-1]["end"],
                "word_times": [(w["start"], w["end"]) for w in chunk],
            }
        )
    return groups


# ---------------------------------------------------------------------------
# Clip creation for one group
# ---------------------------------------------------------------------------


def _create_group_clips(
    *,
    group: dict[str, Any],
    base_clips: list[TextClip],
    highlight_clips: list[TextClip],
    font_str: str,
    font_obj: ImageFont.FreeTypeFont,
    space_width: float,
    center_x: float,
    y_pos: float,
    highlight_color: str,
) -> None:
    """Generate MoviePy TextClips for one word group.

    Two layers per group:
    - **Base**: all words in white, visible for the full group duration.
    - **Highlight**: each word individually in *highlight_color*, visible
      only during that word's spoken time window.
    """
    words: list[str] = group["words"]
    group_start: float = group["start_time"]
    group_end: float = group["end_time"]
    word_times: list[tuple[float, float]] = group["word_times"]

    # Measure word widths
    word_widths = [font_obj.getlength(w) for w in words]
    total_width = sum(word_widths) + space_width * (len(words) - 1)
    padding = 120  # margin from edges

    # --- Check if text overflows canvas — split last word to second line
    overflow = (padding + total_width) > VIDEO_WIDTH
    if overflow and len(words) >= 2:
        first_line_words = words[:-1]
        first_line_widths = word_widths[:-1]
        first_total = sum(first_line_widths) + space_width * (len(first_line_words) - 1)
        last_word = words[-1]
        last_width = word_widths[-1]
    else:
        first_line_words = words
        first_line_widths = word_widths
        first_total = total_width
        last_word = ""
        last_width = 0

    # Fixed pixel Y so all words share the same baseline
    y_first = int(y_pos - CAPTION_Y_OFFSET)
    y_second = (
        int(y_pos + CAPTION_Y_OFFSET) if overflow else int(y_pos - CAPTION_Y_OFFSET)
    )

    # Starting x so the first line is horizontally centred
    start_x = int(center_x - first_total / 2)

    # --- Base white clips (full group duration) ---------------------------
    cursor_x = start_x
    for idx, word in enumerate(first_line_words):
        clip = TextClip(
            text=word,
            color=TEXT_COLOR,
            font=font_str,
            font_size=FONT_SIZE,
            stroke_width=STROKE_WIDTH,
            stroke_color=STROKE_COLOR,
            margin=TEXT_MARGIN,
        )
        clip = clip.with_position((cursor_x, y_first))
        clip = clip.with_start(group_start)
        clip = clip.with_end(group_end)
        base_clips.append(clip)
        cursor_x += first_line_widths[idx] + space_width

    if overflow and last_word:
        last_x = int(center_x - last_width / 2)
        clip = TextClip(
            text=last_word,
            color=TEXT_COLOR,
            font=font_str,
            font_size=FONT_SIZE,
            stroke_width=STROKE_WIDTH,
            stroke_color=STROKE_COLOR,
            margin=TEXT_MARGIN,
        )
        clip = clip.with_position((last_x, y_second))
        clip = clip.with_start(group_start)
        clip = clip.with_end(group_end)
        base_clips.append(clip)

    # --- Highlight clips (per-word timing) --------------------------------
    cursor_x = start_x
    for idx, word in enumerate(first_line_words):
        w_start, w_end = word_times[idx]
        clip = TextClip(
            text=word,
            color=highlight_color,
            font=font_str,
            font_size=FONT_SIZE,
            stroke_width=STROKE_WIDTH,
            stroke_color=STROKE_COLOR,
            margin=TEXT_MARGIN,
        )
        clip = clip.with_position((cursor_x, y_first))
        clip = clip.with_start(w_start)
        clip = clip.with_end(w_end)
        highlight_clips.append(clip)
        cursor_x += first_line_widths[idx] + space_width

    if overflow and last_word:
        last_idx = len(first_line_words)
        w_start, w_end = word_times[last_idx]
        last_x = int(center_x - last_width / 2)
        clip = TextClip(
            text=last_word,
            color=highlight_color,
            font=font_str,
            font_size=FONT_SIZE,
            stroke_width=STROKE_WIDTH,
            stroke_color=STROKE_COLOR,
            margin=TEXT_MARGIN,
        )
        clip = clip.with_position((last_x, y_second))
        clip = clip.with_start(w_start)
        clip = clip.with_end(w_end)
        highlight_clips.append(clip)


# ---------------------------------------------------------------------------
# Script ↔ STT word alignment
# ---------------------------------------------------------------------------


def _align_script_to_stt(
    script: str,
    stt_words: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Align original *script* words with STT timestamps.

    Deepgram STT may mis-hear proper nouns ("OpenAI" → "OpenEye",
    "ChatGPT" → "CheckGPT").  This function uses :mod:`difflib` to map
    each script word to the closest STT timestamp, so captions show the
    *intended* text while keeping accurate word timing.
    """
    if not stt_words:
        return []

    # Tokenise both sequences
    script_words: list[str] = _tokenise_script(script)
    stt_texts: list[str] = [
        _normalise_word(w.get("punctuated_word", w.get("word", ""))) for w in stt_words
    ]

    # Strip punctuation/downcase for comparison only
    script_flat = [_strip_punct(w).lower() for w in script_words]
    stt_flat = [w.lower() for w in stt_texts]

    sm = difflib.SequenceMatcher(None, stt_flat, script_flat)

    aligned: list[dict[str, Any]] = []

    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            # Perfect match — 1:1 mapping
            for offset in range(i2 - i1):
                aligned.append(
                    _make_aligned_word(
                        script_words[j1 + offset], stt_words[i1 + offset]
                    )
                )

        elif tag == "replace":
            # Words differ in this region — map min(len) 1:1, skip extras
            match_count = min(i2 - i1, j2 - j1)
            for offset in range(match_count):
                aligned.append(
                    _make_aligned_word(
                        script_words[j1 + offset], stt_words[i1 + offset]
                    )
                )

        # 'delete': STT has extra words not in script → skip them
        # 'insert': script has words STT missed → ignore (no timestamp)

    if not aligned:
        logger.warning("Word alignment produced zero results; falling back to raw STT")
        return list(stt_words)

    mismatches = sum(
        1
        for a, s in zip(aligned, stt_texts[: len(aligned)], strict=True)
        if _normalise_word(a["punctuated_word"]).lower() != s.lower()
    )
    logger.info(
        "Alignment: %d words, %d corrected from STT",
        len(aligned),
        mismatches,
    )
    return aligned


def _tokenise_script(text: str) -> list[str]:
    """Split *text* into words, keeping original casing & punctuation."""
    return re.findall(r"\S+", text)


def _normalise_word(word: str) -> str:
    """Strip surrounding punctuation but keep internal punctuation."""
    return re.sub(r"^[,.!?;:\"'()\[\]{}]+|[,.!?;:\"'()\[\]{}]+$", "", word)


def _strip_punct(word: str) -> str:
    """Strip all punctuation for comparison."""
    return re.sub(r"[^\w]", "", word)


def _make_aligned_word(
    script_word: str,
    stt_word: dict[str, Any],
) -> dict[str, Any]:
    """Build a word entry with the original script text and STT timing."""
    return {
        "word": script_word,
        "punctuated_word": script_word,
        "start": stt_word["start"],
        "end": stt_word["end"],
        "confidence": stt_word.get("confidence", 1.0),
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_background(
    path: str | Path | None,
    config: Config,
) -> Path | None:
    if path:
        p = Path(path)
        if p.exists():
            return p
        logger.error("Specified background not found: %s", path)
        return None

    clips_dir = config.clips_dir
    if not clips_dir.exists():
        logger.error("No clips directory at %s", clips_dir)
        return None

    candidates = list(clips_dir.glob("*.mp4"))
    if not candidates:
        logger.error("No .mp4 files in %s", clips_dir)
        return None

    chosen = random.choice(candidates)
    logger.info("Selected background: %s", chosen.name)
    return chosen


def _resolve_font(
    path: str | Path | None,
    config: Config,
) -> ImageFont.FreeTypeFont | None:
    if path:
        p = Path(path)
        if not p.exists():
            logger.error("Specified font not found: %s", path)
            return None
        return ImageFont.truetype(str(p), FONT_SIZE)

    fonts_dir = config.fonts_dir
    candidates = list(fonts_dir.glob("*.ttf"))
    if not candidates:
        logger.error("No .ttf font files in %s", fonts_dir)
        return None

    return ImageFont.truetype(str(candidates[0]), FONT_SIZE)


def _make_output_path(config: Config) -> Path:
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    return config.output_dir / f"hn_video_{ts}.mp4"
