# Security

ai-clip-creator is an app you run on your own computer. It has no hosted service
and no shared server.

## Your keys stay on your computer

- API keys (Twitch, Claude, Berget, TikTok, YouTube) are saved in a `.env` file in
  your copy of the repository. The app writes this file with permissions `0600`
  (only your user can read it).
- `.env` is listed in `.gitignore`. Never commit it, and never paste a key into an
  issue, a pull request or a log.
- The app never sends a key value back to the browser, never logs it and never puts
  it in an error message. The settings page only shows whether a key is set.
- The repository runs [gitleaks](https://github.com/gitleaks/gitleaks) in CI and as a
  pre-commit hook (`.gitleaks.toml`) to stop secrets from being committed.

## The API only listens on localhost

- `clipsmith serve` binds to `127.0.0.1` by default, so other machines on your
  network cannot reach it. Only pass `--host 0.0.0.0` if you know why you need it,
  and set `CLIPSMITH_API_KEY` when you do.
- CORS allows only the local dashboard (`http://localhost:3000` and
  `http://127.0.0.1:3000`) unless you set `CORS_ORIGINS`.
- **Host check.** The API and the dashboard's `/api` proxy answer 403 to every request
  whose `Host` is not `localhost`, `127.0.0.1` or `[::1]` (any port). This stops DNS
  rebinding, where another web site makes its own name point at your computer.
  `ALLOWED_HOSTS` (comma-separated) adds names for the API; Docker Compose sets it to `api`.
- **Origin check.** A request that changes something (anything but GET, HEAD and
  OPTIONS) and carries an `Origin` header must come from `CORS_ORIGINS`, otherwise
  the API answers 403. Another web page open in your browser cannot post to the app.
  Tools such as `curl` send no `Origin` and still work. The dashboard's `/api` proxy
  does its own check: an `Origin` must be the dashboard's own address (same name and
  port as `Host`), otherwise it answers 403. The proxy does not pass `Origin` on, so
  the dashboard works on any port.
- Endpoints that change keys, and the TikTok and YouTube login (`/connect` and
  `/callback`), require a loopback address (the same computer).
- When `CLIPSMITH_API_KEY` is set, the endpoints that change data (runs, clips,
  posts, uploads, keys, disconnecting an account) require the `X-Api-Key` header.
  The dashboard's proxy adds it from `API_KEY`. The TikTok and YouTube login does
  not use `X-Api-Key`: the browser opens it straight against the API, so it is
  protected by the loopback check and a one-time `state` value instead.
- Docker Compose publishes the ports on `127.0.0.1` only.

## Docker

Inside Docker the loopback check cannot work as it is: the dashboard's proxy calls the API as
`http://api:8000` from the web container, and a browser on your computer reaches the API through Docker's
gateway address, never through `127.0.0.1`. So:

- **Keys.** Docker Compose sets `TRUSTED_PROXY_HOSTS=api` and `TRUSTED_PROXY_NET=172.29.0.0/24`, and
  gives the Compose network that fixed subnet. The key endpoints then also accept a request whose `Host` is
  exactly `api` and whose address is inside that subnet. Two kinds of callers have such an address: the
  containers on the Compose network (the dashboard's proxy), and this computer, because a request to the
  port on `127.0.0.1` reaches the API from Docker's gateway (`172.29.0.1`). So only this computer and the
  containers on the Compose network can send `Host: api` and save keys. Other computers on your network
  cannot, as long as the ports stay published on `127.0.0.1`: **never publish them on `0.0.0.0`** (for
  example `"8000:8000"` without the address). The proxy has already checked `Host` and `Origin`, so another
  web page still cannot save keys. Local names such as `localhost` are never trusted this way; they always
  need a loopback address. Without both variables (outside Docker) no proxy is trusted.
- **Where keys are saved.** Compose sets `KLIPP_ENV_FILE=/app/data/.env`, so keys saved on the Settings
  page go to `data/.env` on your computer (`data/` is mounted) and are loaded again on the next start.
  A `.env` in the project folder is read too, if you have one.
- **TikTok and YouTube login does not work in Docker.** The browser opens `/connect` straight against the
  API, and TikTok or Google sends it back to `/callback` there, so the request comes from Docker's gateway
  with `Host: localhost`. That is not a loopback address, and trusting the gateway would also trust every
  other container. Connect the accounts once without Docker (`scripts/start.sh` or `scripts/start.ps1`):
  the tokens are saved in `klippdata/`, which Docker mounts, so the app in Docker can post with them.
- **New campaigns.** Creating a campaign in the app also writes `config.<name>.yaml` in the project folder.
  In Docker that folder is inside the image, so add new campaigns without Docker (or copy the file next to
  the code and rebuild). Editing existing campaigns and uploading overlays works: `profiles/` and
  `overlays/` are mounted.
- The API container runs as an unprivileged user. Its entrypoint starts as root only to hand newly created
  mount folders to that user.

## Reporting a vulnerability

Please do not open a public issue for a security problem.

Report it privately through GitHub: open the repository's **Security** tab and
choose **Report a vulnerability**. Include what you found, how to reproduce it and
which version or commit you tested. You will get an answer as soon as possible,
and the fix will be credited to you if you want.

If you find a key or password in the repository or its history, report it the
same way. Do not include the value itself in the report.
