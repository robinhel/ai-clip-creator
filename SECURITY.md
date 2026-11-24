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

## Reporting a vulnerability

Please do not open a public issue for a security problem.

Report it privately through GitHub: open the repository's **Security** tab and
choose **Report a vulnerability**. Include what you found, how to reproduce it and
which version or commit you tested. You will get an answer as soon as possible,
and the fix will be credited to you if you want.

If you find a key or password in the repository or its history, report it the
same way. Do not include the value itself in the report.
