# Start ai-clip-creator without Docker (Windows PowerShell).
#   powershell -ExecutionPolicy Bypass -File scripts\start.ps1
# Needs Python 3.11+, ffmpeg, Node.js and pnpm. Creates .venv the first time, installs everything,
# starts the API on http://127.0.0.1:8000 and the dashboard on http://127.0.0.1:3000, and opens the browser.
# Stop both with Ctrl+C.
$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

function Fail([string]$Message) {
    Write-Host "Error: $Message" -ForegroundColor Red
    exit 1
}

function Has([string]$Name) {
    return [bool](Get-Command $Name -ErrorAction SilentlyContinue)
}

# ---------- tools ----------
$Python = $null
foreach ($candidate in @("py", "python", "python3")) {
    if (-not (Has $candidate)) { continue }
    $pyArgs = @()
    if ($candidate -eq "py") { $pyArgs = @("-3") }
    # Windows PowerShell 5.1 turns stderr into an error with "Stop": the Store's "python" shortcut and
    # "py -3" without Python 3 write there. So this check runs with "Continue" and in try/catch.
    $ErrorActionPreference = "Continue"
    try {
        & $candidate @pyArgs -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" 2>$null
        $ok = ($LASTEXITCODE -eq 0)
    } catch {
        $ok = $false
    } finally {
        $ErrorActionPreference = "Stop"
    }
    if ($ok) { $Python = @($candidate) + $pyArgs; break }
}
if (-not $Python) { Fail "Python 3.11 or newer was not found. Install it from https://www.python.org/downloads/" }
if (-not (Has "ffmpeg")) { Fail "ffmpeg was not found. Install it: winget install Gyan.FFmpeg, then open a new terminal." }
if (-not (Has "ffprobe")) { Fail "ffprobe was not found. It comes with ffmpeg: install ffmpeg again." }
if (-not (Has "node")) { Fail "Node.js was not found. Install Node.js 20 or newer from https://nodejs.org/" }
if (-not (Has "pnpm")) { Fail "pnpm was not found. Install it: corepack enable pnpm (or npm install -g pnpm)." }

# ---------- Python ----------
$VenvPython = Join-Path $Root ".venv\Scripts\python.exe"
$UseUv = Has "uv"
if (-not (Test-Path $VenvPython)) {
    Write-Host "Creating .venv ..."
    if ($UseUv) { uv venv .venv } else { & $Python[0] @($Python | Select-Object -Skip 1) -m venv .venv }
    if ($LASTEXITCODE -ne 0) { Fail "Could not create .venv." }
}

Write-Host "Installing the Python packages (the first time takes a few minutes) ..."
if ($UseUv) {
    uv pip install --python $VenvPython -q -e ".[server,vision]" -r requirements-campaign.txt
} else {
    & $VenvPython -m pip install -q -e ".[server,vision]" -r requirements-campaign.txt
}
if ($LASTEXITCODE -ne 0) { Fail "Could not install the Python packages. Read the message above." }

# ---------- dashboard ----------
Write-Host "Installing the dashboard packages ..."
$env:CYPRESS_INSTALL_BINARY = "0"  # the Cypress test browser is not needed to run the app
Push-Location web
pnpm install --frozen-lockfile
$code = $LASTEXITCODE
Pop-Location
if ($code -ne 0) { Fail "pnpm install failed in web\. Read the message above." }

foreach ($dir in @("work", "out", "klippdata", "data")) {
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
}

# ---------- start ----------
$env:KLIPP_ROOT = $Root
$env:NEXT_PUBLIC_API_BASE = "http://127.0.0.1:8000"
$Clipsmith = Join-Path $Root ".venv\Scripts\clipsmith.exe"

Write-Host "Starting the API on http://127.0.0.1:8000 ..."
$api = Start-Process -FilePath $Clipsmith -ArgumentList "serve", "--host", "127.0.0.1", "--port", "8000" `
    -NoNewWindow -PassThru
Write-Host "Starting the dashboard on http://127.0.0.1:3000 ..."
$web = Start-Process -FilePath "cmd.exe" -ArgumentList "/c", "pnpm dev --port 3000" `
    -WorkingDirectory (Join-Path $Root "web") -NoNewWindow -PassThru

$Url = "http://localhost:3000"

function Test-Url([string]$Address) {
    try {
        Invoke-WebRequest -Uri $Address -UseBasicParsing -TimeoutSec 2 | Out-Null
        return $true
    } catch {
        return $false
    }
}

try {
    $ready = $false
    for ($i = 0; $i -lt 120; $i++) {
        if ($api.HasExited -or $web.HasExited) { Fail "The API or the dashboard stopped. Read the message above." }
        if ((Test-Url "http://127.0.0.1:8000/health") -and (Test-Url $Url)) { $ready = $true; break }
        Start-Sleep -Seconds 1
    }
    if (-not $ready) { Fail "The API or the dashboard did not start within 2 minutes. Read the messages above." }
    Start-Process $Url
    Write-Host ""
    Write-Host "ai-clip-creator is running: $Url"
    Write-Host "Add your keys on the Settings page. Press Ctrl+C to stop."
    # Stop as soon as one of them stops; finally stops the other.
    while (-not ($api.HasExited -or $web.HasExited)) { Start-Sleep -Seconds 1 }
    Write-Host "The API or the dashboard stopped. Stopping the other." -ForegroundColor Red
} finally {
    $ErrorActionPreference = "Continue"  # taskkill writes to stderr when the process is already gone
    foreach ($p in @($api, $web)) {
        try {
            if ($p -and -not $p.HasExited) { & taskkill /PID $p.Id /T /F 2>$null | Out-Null }
        } catch { }
    }
}
