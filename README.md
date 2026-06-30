# Audience Deck

Real-time audience-directed presentation slide deck. Speaker pushes prompts, audience submits responses, DeepSeek API generates slide content with full narrative continuity.

## What it does

A presenter pushes polls or questions to an audience. The audience submits responses. DeepSeek API synthesizes the input and generates the next presentation slide — with animated charts, word clouds, and bulleted summaries. Each slide builds on the full narrative of all previous slides.

## Files

- **speaker.html** — Speaker panel (admin + display). All-in-one: API key entry, poll/wordcloud/rating/question builder, content moderation queue, slide rendering, PDF export, 5 themes, presenter notes, keyboard shortcuts. Works on `file://`.
- **audience.html** — Audience interface. Mobile card UI. Shared state with speaker via localStorage. Batch submit for testing. Theme sync with speaker.
- **server.py** — Optional Python HTTP server with DeepSeek API integration, admin token protection, thread-safe state.
- **demo.html** — Original 3-panel all-in-one demo for single-screen testing.

## Quick Start

1. Open `speaker.html` in a browser (double-click or serve via any HTTP server)
2. Enter your DeepSeek API key (stored in localStorage, never sent elsewhere)
3. Set a topic, push a poll
4. Open `audience.html` in another tab
5. Submit responses (or use Batch Submit for testing)
6. Click **Generate Next Slide** (or press `Ctrl+G`)
7. Slides render with animated CSS charts, word clouds, and bulleted summaries

## Features

- **4 interaction types**: Poll, Word Cloud, Rating (1-10), Free-text Q&A
- **4 slide layouts**: Hero, Split-Text (thematic bullets), Chart-Bar (animated), Word Cloud (log-scale)
- **Content moderation**: Review queue with per-item redact/restore before generation
- **Narrative continuity**: Full previous slide content passed as context window
- **Smart truncation**: Summarizes early slides when deck exceeds 4 slides
- **Error recovery**: Retry, manual fallback slide, or dismiss on API failure
- **5 themes**: Dark, Light, Ocean, Forest, Sunset — synced across speaker and audience
- **Keyboard shortcuts**: `Ctrl+G` generate, `Enter` push prompt, `1`-`4` switch type
- **Export**: Print to PDF with `@media print` CSS
- **Presenter notes**: Raw submission data and talking points
- **Profanity filter**: 50-word blocklist for auto-redaction
- **Deterministic layout routing**: Layout forced by prompt type, not LLM choice

## Server Mode (optional)

```bash
# Set API key
export DEEPSEEK_API_KEY=sk-...

# Start server
python3 server.py --port 8091 --admin-token mytoken

# Open in browser
# Speaker: http://localhost:8091/speaker.html
# Audience: http://localhost:8091/audience.html
```

## Architecture

```
Speaker (speaker.html)          Audience (audience.html)
       │                                │
       │ Push prompt                    │ Poll localStorage every 1s
       ▼                                ▼
  ┌─────────────────────────────────────────┐
  │         localStorage (shared state)      │
  │  topic, promptLive, submissions,        │
  │  slideHistory, currentSlide             │
  └─────────────────────────────────────────┘
       │                                │
       │ Generate (Ctrl+G)              │ Submit responses
       ▼                                ▼
  DeepSeek API ─────────────────────→ New slide
  (JSON mode, schema-locked)           rendered on both
```

## Design Decisions

- **Co-pilot mode**: Presenter triggers generation, preventing chaos from auto-generation
- **Component-slot hybrid**: Fixed layout types, dynamic content from structured JSON
- **Batch-on-trigger**: Audience input accumulates as snapshot
- **Pure CSS/JS**: No charting libraries — animated via CSS transitions
- **localStorage bridge**: Zero-server state sharing for local testing
- **Log-scale word cloud**: Minority responses stay visible at 14px minimum
- **Percentage chart labels**: Raw counts + computed percentages

## License

MIT
