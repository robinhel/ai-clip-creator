#!/usr/bin/env bash
# Löser resten: kopplar klonen till ditt eget repo, laddar upp allt,
# bygger Python-miljön och kör testerna.
#
# Kör i mappen clipsmith:
#     bash fortsatt.sh
#
# Skriptet går att köra flera gånger. Det hoppar över steg som redan är gjorda.

set -euo pipefail

ORIGIN_URL="${ORIGIN_URL:-git@github.com:robinhel/ai-clip-creator.git}"
BARA_GIT="${BARA_GIT:-0}"       # BARA_GIT=1 hoppar över miljö och tester
PY="${PYTHON:-python3}"

say()  { printf '\n== %s\n' "$1"; }
stop() { printf '\nSTOPP: %s\n' "$1" >&2; exit 1; }

[ -d src/clipsmith ] && [ -d campaign ] \
  || stop "Kör skriptet i mappen clipsmith, där mappen campaign/ ligger."

# ---------------------------------------------------------------------------
say "1/5 Kopplar klonen till ditt repo"
current="$(git remote get-url origin 2>/dev/null || true)"
if [ "$current" = "$ORIGIN_URL" ]; then
  echo "origin pekar redan på ditt repo."
else
  if [ -n "$current" ]; then
    if git remote get-url upstream >/dev/null 2>&1; then
      git remote remove origin
    else
      git remote rename origin upstream     # originalet heter upstream från och med nu
    fi
  fi
  git remote add origin "$ORIGIN_URL"
fi
git remote -v

# ---------------------------------------------------------------------------
say "2/5 Sparar ändringarna i git"
if grep -q '^cloud:' config.yaml 2>/dev/null; then
  stop "config.yaml har kvar delen 'cloud:' med originalets inloggningsuppgift. Ta bort den delen först."
fi
git add -A
if git diff --cached --name-only | grep -qx '.env'; then
  git reset -q
  stop ".env är på väg in i git. Kontrollera att .env står i .gitignore."
fi
if git diff --cached --quiet; then
  echo "Inget nytt att spara."
else
  git commit -q -m "Kampanjlager ovanpå clipsmith: profil, overlay, caption, regler, klipplogg, budget"
  echo "Sparat."
fi

# Allt hamnar på grenen main, så att du bara har en gren att hålla reda på.
branch="$(git branch --show-current)"
if [ "$branch" != "main" ]; then
  git checkout -q main
  git merge -q --ff-only "$branch"
  git branch -q -d "$branch"
  echo "Flyttade ändringarna från $branch till main."
fi

# ---------------------------------------------------------------------------
say "3/5 Laddar upp till GitHub"
if ! git push -u origin main; then
  stop "Uppladdningen misslyckades. Vanliga orsaker: repot på GitHub är inte tomt, eller så saknas din SSH-nyckel på GitHub."
fi

if [ "$BARA_GIT" = "1" ]; then
  echo "Klart (bara git)."
  exit 0
fi

# ---------------------------------------------------------------------------
say "4/5 Bygger Python-miljön (tar några minuter)"
"$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' \
  || stop "Python 3.11 eller nyare behövs, du har $("$PY" --version 2>&1). Kör: brew install python@3.12 och sedan: PYTHON=python3.12 bash fortsatt.sh"
[ -d .venv ] || "$PY" -m venv .venv
.venv/bin/python -m pip install -q --upgrade pip
if ! .venv/bin/python -m pip install -q -e ".[dev,vision]"; then
  echo "Alla extrapaket gick inte att installera. Provar med det nödvändigaste."
  .venv/bin/python -m pip install -q -e . pytest pytest-asyncio opencv-python-headless
fi
.venv/bin/python -m pip install -q -r requirements-campaign.txt
command -v ffmpeg >/dev/null || echo "OBS: ffmpeg saknas. Installera med: brew install ffmpeg"

# ---------------------------------------------------------------------------
say "5/5 Kör testerna"
set +e
.venv/bin/python -m pytest tests_campaign -q
own=$?
.venv/bin/python -m pytest tests/unit -q > .clipsmith-tester.log 2>&1
theirs=$?
set -e
tail -n 5 .clipsmith-tester.log
rm -f .clipsmith-tester.log

echo
[ "$own" -eq 0 ]    && echo "OK   kampanjlagrets tester" || echo "FEL  kampanjlagrets tester"
[ "$theirs" -eq 0 ] && echo "OK   clipsmiths tester" \
                    || echo "FEL  clipsmiths tester (ett test om 'Spanish' väntas falla, se docs/klipp/OMBYGGNAD.md uppgift 1)"

cat <<'TEXT'

Nästa steg:
  1. Öppna .env och fyll i ANTHROPIC_API_KEY och Twitch-nycklarna.
  2. Sätt en utgiftsgräns i Claude Console.
  3. Starta Claude Code i den här mappen:   claude
     och skriv:   Läs FORTSATT.md och gör uppgifterna under "Till Claude Code".
TEXT
