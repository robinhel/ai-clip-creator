# Getting Started

There are two ways to run ai-clip-creator: with Docker (one command) or with a start script.
Both start the API on `127.0.0.1:8000` and the dashboard on <http://localhost:3000>.

## With Docker

You need Docker Desktop, or Docker Engine with Compose v2.24 or newer.

```sh
git clone https://github.com/robinhel/ai-clip-creator
cd ai-clip-creator
docker compose up
```

Open <http://localhost:3000> and paste your keys on the **Settings** page. They are saved in `data/.env`.
Your files are in `work/`, `out/`, `klippdata/` and `data/` next to the code.

To use the published images instead of building:

```sh
docker compose -f docker-compose.release.yml up
```

This needs the packages `ai-clip-creator-api` and `ai-clip-creator-web` to be public. GitHub makes new
packages private, so the repository owner changes that once after the first release.

!!! note "TikTok and YouTube in Docker"
    Connecting a TikTok or YouTube account only works without Docker. Connect once with the start
    script below; the login is saved in `klippdata/`, which Docker uses too. See `SECURITY.md`.

## Without Docker

| Tool | Install |
|------|---------|
| Python 3.11+ | [python.org](https://python.org) or `winget install Python.Python.3` |
| ffmpeg | `brew install ffmpeg`, `sudo apt install ffmpeg` or `winget install Gyan.FFmpeg` — must be on `PATH` |
| Node.js 20+ | [nodejs.org](https://nodejs.org) |
| pnpm | `corepack enable pnpm` |

```sh
git clone https://github.com/robinhel/ai-clip-creator
cd ai-clip-creator
./scripts/start.sh                                            # macOS and Linux
powershell -ExecutionPolicy Bypass -File scripts\start.ps1    # Windows
```

The script checks the tools, creates `.venv`, installs the Python and dashboard packages, starts both
servers and opens the browser. Ctrl+C stops them. Keys you save on the **Settings** page go to `.env`.

## Keys

| Key | Where to get it |
|-----|-----------------|
| Twitch Client ID and secret | <https://dev.twitch.tv/console> → Register Your Application, redirect `http://localhost` |
| Claude API key | <https://console.anthropic.com> |
| Berget API key (transcription) | <https://berget.ai> |
| TikTok Client key and secret (optional) | <https://developers.tiktok.com> |
| YouTube Client ID and secret (optional) | <https://console.cloud.google.com> |

## Command line

You do not need these to use the dashboard. They come from clipsmith, which this app is built on; run them
inside `.venv` (`. .venv/bin/activate`). The campaign layer's commands (`python -m campaign ...`) are in
the README.

## First-time setup (command line)

Run the setup wizard once to configure your LLM provider and verify ffmpeg:

```sh
clipsmith setup
```

You'll be asked to choose a provider (`anthropic`, `openai`, or `ollama`) and paste your API key.

- Anthropic API keys: <https://console.anthropic.com>
- OpenAI API keys: <https://platform.openai.com>
- Ollama (local, free): `clipsmith setup --provider ollama`

The key is saved to `.env` next to the executable.

## Process a local MP4

```sh
clipsmith process "C:\path\to\your_recording.mp4"
```

Runs the full pipeline — transcribe → score candidates → LLM selection → cut clips.
Output goes to `out\<video_id>\` (the video_id is the filename without extension).

!!! note "First run"
    The first time you run, faster-whisper downloads the transcription model (~500 MB) to
    `%USERPROFILE%\.cache\huggingface`. Subsequent runs use the cached copy.

## Process a Twitch VOD directly

```sh
clipsmith run-vod <video_id>
```

Downloads the VOD from Twitch (requires `TWITCH_CLIENT_ID` and `TWITCH_CLIENT_SECRET` in `.env`)
and runs the full pipeline.

## Daemon mode

```sh
clipsmith watch
```

Polls Twitch every `poll_interval_s` seconds (default 120). When a new archive VOD appears
it runs the pipeline automatically. Processed VODs are tracked in `state.json`.

## Reprocessing without re-running everything

```sh
# Re-cut clips only (e.g. after changing caption settings)
clipsmith clip <video_id>

# Re-run from existing download, redo transcription + LLM
clipsmith run-vod --local --skip-download <video_id>
```

## Stacked layout (webcam + gameplay)

After reviewing the flat clips, reframe selected ones into a two-panel 9:16 layout:

```sh
clipsmith reframe <video_id> clip_01 clip_04 clip_09
```

Output goes to `out\<video_id>\stacked\`.

**Auto-detecting the webcam rectangle:**

```sh
clipsmith detect-webcam <video_id>
```

This samples frames, detects the face rectangle using OpenCV, and writes `reframe.webcam_rect`
directly into `config.yaml`. Requires `pip install -e ".[vision]"`.

## Common troubleshooting

| Symptom | Fix |
|---------|-----|
| `ffmpeg not found` | Ensure `ffmpeg.exe` is on `PATH` (`winget install Gyan.FFmpeg`) |
| API key errors | Re-run `clipsmith setup` and paste the key again |
| Slow first run | Transcription model is downloading — wait for it to finish |
| Stacked clips empty | Set `reframe.webcam_rect` in `config.yaml` or run `detect-webcam` |
| `VOD not found` | Check `TWITCH_CLIENT_ID` / `TWITCH_CLIENT_SECRET` in `.env` |
