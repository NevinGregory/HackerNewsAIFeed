# HN AI Feed

> Convert Hacker News front-page stories into TikTok-style short-form videos
> with Minecraft parkour backgrounds, AI voiceovers, and word-by-word captions.

https://github.com/user-attachments/assets/placeholder

---

## How It Works

```
HN top stories → extract articles → AI writes script → ElevenLabs TTS →
→ word-by-word captions → Minecraft parkour background → vertical .mp4
```

1. **Fetch** — grabs the current top stories from the [Hacker News API](https://github.com/HackerNews/API)
2. **Extract** — downloads each linked article and pulls out the clean text via [trafilatura](https://trafilatura.readthedocs.io/)
3. **Curate** — filters out paywalled, non-English, and code-heavy content
4. **Script** — an LLM (Gemini or OpenAI) converts the long-form article into a punchy 30–60 second short-form script
5. **Voiceover + timestamps** — [ElevenLabs](https://elevenlabs.io) TTS with `convert_with_timestamps` returns both the audio **and** character-level timing in one call — no separate STT step needed, **zero transcription errors**
6. **Compose** — MoviePy layers everything: random Minecraft parkour clip + audio + word-by-word colour-highlighted captions from the *exact script text*
7. **Output** — a 1080×1920 H.264 `.mp4` ready for TikTok, Reels, or Shorts

## Quick Start

### Prerequisites

- **Python 3.11+** and **[uv](https://docs.astral.sh/uv/)** (or pip)
- **[ffmpeg](https://ffmpeg.org/)** installed and on your `$PATH`
- An **ElevenLabs API key** ([sign up](https://elevenlabs.io/app/settings/api-keys) — free tier works with cloned/personal voices)
- A **Gemini API key** ([free tier](https://aistudio.google.com/apikey)) or **OpenAI API key**

### 1. Clone & Install

```bash
git clone https://github.com/yourusername/hn-ai-feed.git
cd hn-ai-feed
uv sync
```

### 2. Configure

```bash
cp .env.example .env
```

Edit `.env` and fill in your API keys:

```ini
# Required
ELEVENLABS_API_KEY=your_key_here

# Choose one script provider
GEMINI_API_KEY=your_gemini_key_here    # recommended (free tier)
# or
OPENAI_API_KEY=your_openai_key_here
```

### 3. Get Background Footage

Download Minecraft parkour clips from YouTube — the composer picks a random one
for each video:

```bash
# Download individual clips
uv run hn-feed download-clip "https://www.youtube.com/watch?v=..."
uv run hn-feed download-clip "https://www.youtube.com/watch?v=..."

# Or drop your own .mp4 files into assets/clips/
```

You'll want at least 2–3 background clips (10+ minutes each works best).

### 4. Add a Font

Place a `.ttf` font in `assets/fonts/`. The composer uses the first one it finds.
Futura Extra Bold is recommended for the classic TikTok caption look.

### 5. Generate Videos

```bash
# Generate videos from the current HN top 5 stories
uv run hn-feed run --count 5

# Process a single article by URL
uv run hn-feed run --url https://example.com/tech-article

# Preview what would be processed (no API calls)
uv run hn-feed run --dry-run

# List previously processed stories
uv run hn-feed list
uv run hn-feed list --status rendered
```

### 6. Find Your Videos

Output lands in `videos/`:

```
videos/
├── hn_video_20250101-143052.mp4
├── hn_video_20250101-143158.mp4
└── ...
```

---

## Architecture

```
src/
├── main.py               # CLI entry point (click)
├── config.py             # .env loader, typed config singleton
├── db.py                 # SQLite persistence for HN stories
│
├── fetcher.py            # HN Firebase REST API client
├── extractor.py          # trafilatura article extraction + fallback
├── curator.py            # Story filtering (paywalls, language, code)
├── script_writer.py      # LLM prompt → short-form script (Gemini/OpenAI)
├── voice_generator.py    # ElevenLabs TTS + word timestamps (single API call)
├── transcriber.py        # *(legacy)* Deepgram STT — no longer used in pipeline
├── composer.py           # MoviePy composition: bg + audio + captions
└── pipeline.py           # End-to-end orchestrator with checkpointing
```

Each pipeline stage updates a `status` field in the database, so interrupted
runs **resume from the last successful checkpoint** — no re-fetching, no
re-generating.

---

## Configuration Reference

| Variable | Default | Description |
|----------|---------|-------------|
| `ELEVENLABS_API_KEY` | *(required)* | ElevenLabs API key for TTS |
| `ELEVENLABS_VOICE_ID` | `21m00Tcm4TlvDq8ikWAM` | ElevenLabs voice ID |
| `ELEVENLABS_VOICE_MODEL` | `eleven_turbo_v2_5` | ElevenLabs model (consider `eleven_multilingual_v2` for 70+ languages) |
| `GEMINI_API_KEY` | — | Google Gemini key for script generation |
| `OPENAI_API_KEY` | — | OpenAI key for script generation |
| `SCRIPT_PROVIDER` | `gemini` | `"gemini"` or `"openai"` |
| `GEMINI_MODEL` | `gemini-2.0-flash` | Gemini model name |
| `OPENAI_MODEL` | `gpt-4o-mini` | OpenAI model name |
| `DEEPGRAM_API_KEY` | — | *(optional)* Deepgram key — only needed if reverting to legacy STT-based captions |
| `HN_TOP_STORIES_URL` | `https://hacker-news.firebaseio.com/v0/topstories.json` | HN API endpoint |
| `HN_ITEM_URL` | `https://hacker-news.firebaseio.com/v0/item/{id}.json` | HN item endpoint |
| `OUTPUT_DIR` | `videos` | Where final `.mp4`s go |
| `CACHE_DIR` | `cache` | Audio + word-data cache and SQLite database |

---

## Customization

### Change the Caption Style

Edit the constants at the top of `src/composer.py`:

```python
FONT_SIZE = 68          # Caption text size
HIGHLIGHT_COLOR = "#00FF66"  # Green highlight — change to your brand colour
WORDS_PER_GROUP = 3     # How many words shown at once (2 for faster pacing)
```

### Use a Different Voice

List available voices:

```bash
uv run python -c "
from elevenlabs.client import ElevenLabs
from src.config import get_config
cfg = get_config()
client = ElevenLabs(api_key=cfg.elevenlabs_api_key)
for v in client.voices.get_all().voices:
    print(f'{v.name:40s} | {v.voice_id:30s} | {v.category}')
"
```

Set the one you want in `.env`:

```ini
ELEVENLABS_VOICE_ID=your_chosen_voice_id
```

> On the free tier, **cloned/generated** voices only. Premade library voices
> require a paid subscription.

### Use a Different LLM for Scripts

Set `SCRIPT_PROVIDER=openai` in `.env` to use OpenAI instead of Gemini.

### Add Your Own Backgrounds

Drop any `.mp4` into `assets/clips/` — the composer picks randomly.
Minecraft parkour works great, but any gameplay, satisfying footage, or
slow-motion video will work.

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| **"No .mp4 files in clips dir"** | Download background clips with `hn-feed download-clip <url>` |
| **"No .ttf font files"** | Place a `.ttf` font in `assets/fonts/` |
| **ElevenLabs 402 error** | Free plan can't use library voices — clone a voice in the dashboard or upgrade |
| **ElevenLabs auth error** | Check `ELEVENLABS_API_KEY` in `.env` |
| **Gemini 403 error** | Check `GEMINI_API_KEY` in `.env` |
| **"Curation rejected: No extractable article content"** | Article may be paywalled or JS-rendered — try another story |
| **ffmpeg not found** | Install ffmpeg: `brew install ffmpeg` (macOS) or `apt install ffmpeg` (Linux) |

---

## License

MIT — see [LICENSE](LICENSE) for details.

## Credits

Built on the video composition approach pioneered by [sarrtle/vidgen](https://github.com/sarrtle/vidgen)
and [janmartchouk/vidgen](https://github.com/janmartchouk/vidgen).
