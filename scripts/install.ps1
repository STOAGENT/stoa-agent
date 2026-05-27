# STOA Agent installer — Windows PowerShell
# Six sovereign LLMs as your local agent. Council-mode default. On-chain verifiable.
#
# Usage:
#   iex (irm https://raw.githubusercontent.com/STOAGENT/stoa-agent/master/scripts/install.ps1)
#
# What it does:
#   1. Checks for Python 3.11+ (auto-installs via winget if missing)
#   2. Checks for git (auto-installs via winget if missing)
#   3. Installs uv (Astral's fast Python package manager)
#   4. Clones the repo into ~/.stoa/stoa-agent
#   5. Creates a venv + installs stoa-agent into it
#   6. Wires the `stoa` command onto PATH
#
# Safety properties:
#   - NEVER uses `exit` or `$ErrorActionPreference = 'Stop'` in a way that
#     terminates the host shell. On error, prints the diagnosis + pauses
#     for `Read-Host` so the user can READ what went wrong before closing.
#   - All external network calls show what they're fetching first.
#   - If a prereq cannot be auto-installed (e.g. winget itself is missing
#     on Windows < 10 1809), prints a precise manual remediation step.
#
# Repo: https://github.com/STOAGENT/stoa-agent
# Docs: https://stoax.xyz/docs

# Continue on errors — we catch + display them ourselves so the window
# never slams shut without the user seeing why.
$ErrorActionPreference = "Continue"
$ProgressPreference    = "SilentlyContinue"

# ─── ui helpers ──────────────────────────────────────────────────────

function Write-StoaInfo($Text) { Write-Host "  $Text" -ForegroundColor White }
function Write-StoaOk($Text)   { Write-Host "  $Text" -ForegroundColor Green }
function Write-StoaWarn($Text) { Write-Host "  $Text" -ForegroundColor Yellow }
function Write-StoaBad($Text)  { Write-Host "  $Text" -ForegroundColor Red }
function Write-StoaDim($Text)  { Write-Host "  $Text" -ForegroundColor DarkGray }

function Write-StoaBanner {
    Write-Host ""
    Write-Host "  STOA Agent installer" -ForegroundColor Yellow
    Write-Host "  six sovereign LLMs - one agent - on-chain verifiable" -ForegroundColor DarkGray
    Write-Host ""
}

function Pause-IfInteractive($Label = "Press Enter to close this window") {
    try {
        if ($Host.Name -eq "ConsoleHost") {
            Write-Host ""
            Read-Host $Label | Out-Null
        }
    } catch { }
}

function Stop-WithError($Message) {
    Write-Host ""
    Write-StoaBad "X $Message"
    Pause-IfInteractive "Read the message above, then press Enter to close"
    throw $Message
}

# ─── prereq: winget (the bootstrap dependency) ───────────────────────

function Test-Winget {
    if (Get-Command winget -ErrorAction SilentlyContinue) { return $true }
    return $false
}

function Install-PrereqViaWinget($PackageId, $FriendlyName, $ManualUrl) {
    if (-not (Test-Winget)) {
        Stop-WithError @"
$FriendlyName is missing and winget (Windows Package Manager) is also
not available on this machine, so we can't auto-install it.

  Manual fix (one time, 2 minutes):
    1. Install '$FriendlyName' from:
       $ManualUrl
    2. Close + reopen PowerShell so PATH refreshes.
    3. Re-run this installer.

  Or: install winget itself first via the Microsoft Store
  ('App Installer'), then re-run this script.
"@
    }
    Write-StoaInfo "Installing $FriendlyName via winget..."
    & winget install --id $PackageId --silent --accept-package-agreements --accept-source-agreements 2>&1 | ForEach-Object { Write-StoaDim "    $_" }
    if ($LASTEXITCODE -ne 0) {
        Stop-WithError "winget failed to install $FriendlyName (exit $LASTEXITCODE). Try installing manually from $ManualUrl and re-run."
    }
    # winget puts new tools on PATH via the user profile; refresh PATH for THIS session.
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path", "User")
}

function Ensure-Python {
    $pyCmd = Get-Command python -ErrorAction SilentlyContinue
    if (-not $pyCmd) {
        Write-StoaWarn "Python not found."
        Install-PrereqViaWinget "Python.Python.3.13" "Python 3.13" "https://www.python.org/downloads/windows/"
        $pyCmd = Get-Command python -ErrorAction SilentlyContinue
        if (-not $pyCmd) {
            Stop-WithError "Python install completed but the python command is still not on PATH. Close + reopen PowerShell and re-run this installer."
        }
    }
    $ver = & python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($ver)) {
        Stop-WithError "Could not determine Python version. Check with 'python --version' and re-run."
    }
    if ([version]$ver -lt [version]"3.11") {
        Stop-WithError "Python 3.11+ is required (you have $ver). Upgrade from https://www.python.org/downloads/windows/ and re-run."
    }
    Write-StoaOk "Python $ver OK"
}

function Ensure-Git {
    if (Get-Command git -ErrorAction SilentlyContinue) {
        Write-StoaOk "git OK"
        return
    }
    Write-StoaWarn "git not found."
    Install-PrereqViaWinget "Git.Git" "Git" "https://git-scm.com/download/win"
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
        Stop-WithError "git install completed but the git command is still not on PATH. Close + reopen PowerShell and re-run."
    }
    Write-StoaOk "git OK"
}

function Ensure-Uv {
    if (Get-Command uv -ErrorAction SilentlyContinue) {
        Write-StoaOk "uv OK"
        return
    }
    Write-StoaInfo "Installing uv (fast Python package manager)..."
    try {
        irm https://astral.sh/uv/install.ps1 | iex
    } catch {
        Stop-WithError "uv install failed: $_. Install manually from https://docs.astral.sh/uv/getting-started/installation/ and re-run."
    }
    $uvBin = Join-Path $env:USERPROFILE ".local\bin"
    if (Test-Path $uvBin) { $env:Path = "$uvBin;$env:Path" }
    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
        Stop-WithError "uv install completed but the uv command is still not on PATH. Close + reopen PowerShell and re-run."
    }
    Write-StoaOk "uv OK"
}

# ─── main install ────────────────────────────────────────────────────

function Install-StoaAgent {
    Write-StoaBanner

    Write-StoaInfo "Checking prerequisites..."
    Ensure-Python
    Ensure-Git
    Ensure-Uv
    Write-Host ""

    $StoaRepo   = "https://github.com/STOAGENT/stoa-agent.git"
    $StoaHome   = if ($env:STOA_HOME)        { $env:STOA_HOME }        else { Join-Path $HOME ".stoa" }
    $InstallDir = if ($env:STOA_INSTALL_DIR) { $env:STOA_INSTALL_DIR } else { Join-Path $StoaHome "stoa-agent" }

    if (Test-Path $InstallDir) {
        Write-StoaInfo "Updating existing install at $InstallDir..."
        Push-Location $InstallDir
        & git fetch origin 2>$null
        & git reset --hard origin/master 2>$null
        Pop-Location
    } else {
        Write-StoaInfo "Cloning $StoaRepo into $InstallDir..."
        & git clone --depth 1 $StoaRepo $InstallDir
        if ($LASTEXITCODE -ne 0) {
            Stop-WithError "git clone failed (exit $LASTEXITCODE). Check your internet + GitHub access, then re-run."
        }
    }

    Write-StoaInfo "Creating venv + installing stoa-agent..."
    Push-Location $InstallDir
    try {
        & uv venv .venv --python 3.13 2>&1 | ForEach-Object { Write-StoaDim "    $_" }
        if ($LASTEXITCODE -ne 0) {
            Stop-WithError "uv venv failed. Check disk space / permissions and re-run."
        }
        & uv pip install --python .venv\Scripts\python.exe -e . 2>&1 | ForEach-Object { Write-StoaDim "    $_" }
        if ($LASTEXITCODE -ne 0) {
            Stop-WithError "uv pip install failed. Inspect the error above and re-run."
        }
    } finally {
        Pop-Location
    }

    # PATH wiring — drop a small launcher into a stable dir on PATH.
    $LauncherDir = Join-Path $env:LOCALAPPDATA "stoa\bin"
    if (-not (Test-Path $LauncherDir)) { New-Item -ItemType Directory -Force -Path $LauncherDir | Out-Null }
    $StoaCmd = Join-Path $LauncherDir "stoa.cmd"
    $StoaExe = Join-Path $InstallDir ".venv\Scripts\stoa.exe"
    Set-Content -Path $StoaCmd -Encoding ASCII -Value "@echo off`r`n`"$StoaExe`" %*"

    $userPath = [System.Environment]::GetEnvironmentVariable("Path", "User")
    if (-not ($userPath -split ";" | Where-Object { $_ -eq $LauncherDir })) {
        $newPath = if ([string]::IsNullOrWhiteSpace($userPath)) { $LauncherDir } else { "$userPath;$LauncherDir" }
        [System.Environment]::SetEnvironmentVariable("Path", $newPath, "User")
        $env:Path = "$env:Path;$LauncherDir"
    }

    Write-Host ""
    Write-StoaOk "STOA Agent installed."
    Write-StoaDim "    Install dir : $InstallDir"
    Write-StoaDim "    Launcher    : $StoaCmd"
    Write-Host ""
    Write-StoaInfo "Next:"
    Write-StoaDim "    stoa             # start chatting (runs setup wizard on first run)"
    Write-StoaDim "    stoa setup       # re-run interactive setup"
    Write-StoaDim "    stoa gateway     # connect Telegram / Discord / Slack / ..."
    Write-Host ""
    Write-StoaDim "  If the stoa command isn't found, close and reopen this terminal"
    Write-StoaDim "  so PATH refreshes."
    Write-Host ""
    Pause-IfInteractive "Press Enter to close"
}

try {
    Install-StoaAgent
} catch {
    # All Stop-WithError throws land here. The error is already printed +
    # paused — we swallow the exception so the script exits cleanly.
}
