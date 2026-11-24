# ai-clip-creator

Turns a Twitch stream into short, vertical clips that are ready to post: it finds the best moments, cuts
them to 9:16, adds the campaign's overlay, hook text and captions, checks them against the campaign rules,
and waits for your yes before anything is posted.

<!-- Screenshots and a GIF go here. Put the files in docs/images/ and remove the comment markers.
![Dashboard](docs/images/dashboard.png)
![Approve queue](docs/images/approve.png)
![How it works](docs/images/how-it-works.gif)
-->

## Quick start (Docker)

You need [Docker Desktop](https://www.docker.com/products/docker-desktop/) (or Docker Engine with Compose v2.24+).

```sh
git clone https://github.com/robinhel/ai-clip-creator
cd ai-clip-creator
docker compose up
```

The first build takes a while (it downloads ffmpeg and a Whisper model). Then:

1. Open <http://localhost:3000>.
2. Go to **Settings** and paste your keys (see [Keys](#keys) below).
3. Pick a campaign and start a run from a Twitch VOD.

Everything the app makes stays in folders next to the code: `work/` (downloads), `out/` (clips),
`klippdata/` (clip log, accounts) and `data/` (the keys you saved in the app, in `data/.env`).
Both ports are open on `127.0.0.1` only, so other computers on your network cannot reach the app.

Ready-made images are published for every release, so you can skip the build:

```sh
docker compose -f docker-compose.release.yml up
```

## Without Docker

You need Python 3.11+, ffmpeg, Node.js 20+ and pnpm.

```sh
./scripts/start.sh                                            # macOS and Linux
powershell -ExecutionPolicy Bypass -File scripts\start.ps1    # Windows
```

The script checks the tools, creates `.venv` (with `uv` if you have it), installs everything, starts the
API on `127.0.0.1:8000` and the dashboard on `127.0.0.1:3000`, and opens your browser. Ctrl+C stops both.

## Keys

Paste them on the **Settings** page. They are saved on your computer only, never shown again and never logged.

| Key | Needed for | Where to get it |
|-----|------------|-----------------|
| Twitch Client ID and secret | Finding and downloading VODs and chat | [dev.twitch.tv/console](https://dev.twitch.tv/console) → Register Your Application (redirect `http://localhost`) |
| Claude API key | Picking moments, frame review, hook text | [console.anthropic.com](https://console.anthropic.com) |
| Berget API key | Transcription | [berget.ai](https://berget.ai) |
| TikTok Client key and secret | Optional: send clips to TikTok | [developers.tiktok.com](https://developers.tiktok.com) (Login Kit + Content Posting API) |
| YouTube Client ID and secret | Optional: upload Shorts | [console.cloud.google.com](https://console.cloud.google.com) → OAuth client (YouTube Data API v3) |

You can also put them in a `.env` file instead; `.env.example` lists the names. A monthly and a daily
budget cap (`BUDGET_USD_PER_MONTH`, `BUDGET_USD_PER_DAY`) stop all Claude calls when they are reached.

## How it works

1. **Download** the VOD and its chat replay from Twitch.
2. **Transcribe** the audio, word by word.
3. **Score moments** with five signals: existing clips, `!clip` commands, chat spikes, hype words and audio energy.
4. **Pick** the best moments with Claude, using the campaign's brief and earlier picks you liked.
5. **Cut** each moment to a vertical 9:16 clip with ffmpeg.
6. **Make it campaign-ready**: overlay, hook text, captions and loudness, from the campaign profile.
7. **Check** it: the rule check (source, length, sound, overlay and on-screen text) and a Claude review of a few frames.
8. **Approve and post**: you say yes or no; approved clips go to TikTok or YouTube, now or on a schedule.

The dashboard's **How it works** page shows the same steps with your own numbers.

## What the campaign layer adds

The `campaign/` folder is built on top of clipsmith:

- **Campaign profiles** (`profiles/*.yaml`): streamer, rules, overlay, text style and captions per campaign, editable in the app.
- **Approve queue**: nothing is posted until the rule check is green and you have said yes.
- **Clip log**: every clip with its status, link, views and likes; `facit/` compares the picks with a hand-made answer key.
- **Editor and versions**: trim a clip, change the hook, or make a new version for another account.
- **Live clips**: watches the chat during a stream and clips a moment soon after it happens.
- **TikTok and YouTube**: connect one or more accounts, post or schedule uploads.
- **Campaign scout**: rates new clipping campaigns and tells you when a good one appears.
- **Budget**: all Claude calls go through one place and stop at the monthly cap.
- **Command line**: `python -m campaign finish | approve | posted | stats | status | check-setup`.

## Security

The app runs on your own computer and only answers requests to `localhost`. Keys stay in `.env` (or
`data/.env` in Docker) with permissions `0600`. Read [SECURITY.md](SECURITY.md) for the details, including
how Docker is handled and how to report a problem.

## Development

```sh
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev,server,vision]" -r requirements-campaign.txt
clipsmith serve                       # API on http://127.0.0.1:8000/docs
cd web && pnpm install && pnpm dev    # dashboard on http://127.0.0.1:3000

python -m pytest tests_campaign -q    # the campaign layer
python -m pytest tests/unit -q        # clipsmith
ruff check src campaign tests tests_campaign
mypy src
cd web && pnpm exec vitest run        # dashboard unit tests
```

More in [docs/getting-started.md](docs/getting-started.md).

## Credits

Built on [clipsmith](https://github.com/ricardogr07/clipsmith) by Ricardo García Ramírez (MIT).
The campaign layer, the dashboard changes and the packaging are by Robin Hellgren. See [LICENSE](LICENSE).
