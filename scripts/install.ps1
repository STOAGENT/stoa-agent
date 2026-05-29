# STOA Agent installer — Windows PowerShell
# Six sovereign LLMs as your local agent. Council-mode default. On-chain verifiable.
#
# Usage:
#   iex (irm https://raw.githubusercontent.com/STOAGENT/stoa-agent/v0.14.7/scripts/install.ps1)
#
# What it does:
#   1. Installs uv (Astral's fast Python package manager) into ~/.local/bin
#   2. Uses `uv python install 3.13` to fetch a portable Python 3.13
#      (no admin rights, no system-wide install, no winget dependency)
#   3. Downloads PortableGit from git-for-windows if git is missing
#      (also no admin, no winget — works on locked-down boxes)
#   4. Clones the repo into %LOCALAPPDATA%\stoa\stoa-agent
#   5. Creates a venv via uv + editable-installs stoa-agent into it
#   6. Wires the `stoa` command onto PATH via %LOCALAPPDATA%\stoa\bin
#
# Safety properties:
#   - NEVER uses `exit` or `$ErrorActionPreference = 'Stop'` in a way that
#     silently terminates the host shell. On error, prints the diagnosis +
#     pauses for `Read-Host` so the user can READ what went wrong.
#   - No winget dependency — works on Windows < 10 1809.
#   - No admin rights — everything lands under %LOCALAPPDATA% and the user's
#     local PATH. Uninstall = `Remove-Item -Recurse %LOCALAPPDATA%\stoa`.
#   - Parity with the Hermes Agent install pattern (the upstream STOA forks
#     from) so behaviour matches what experienced users already expect.
#
# Repo: https://github.com/STOAGENT/stoa-agent
# Docs: https://stoax.xyz/docs

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

# ─── uv (Astral's Python package + interpreter manager) ──────────────

function Ensure-Uv {
    if (Get-Command uv -ErrorAction SilentlyContinue) {
        Write-StoaOk "uv OK"
        return
    }
    # Common existing locations the official installer drops uv into.
    $uvCandidates = @(
        (Join-Path $env:USERPROFILE ".local\bin\uv.exe"),
        (Join-Path $env:USERPROFILE ".cargo\bin\uv.exe")
    )
    foreach ($c in $uvCandidates) {
        if (Test-Path $c) {
            $dir = Split-Path $c
            $env:Path = "$dir;$env:Path"
            if (Get-Command uv -ErrorAction SilentlyContinue) {
                Write-StoaOk "uv OK"
                return
            }
        }
    }
    Write-StoaInfo "Installing uv (fast Python interpreter + package manager)..."
    try {
        irm https://astral.sh/uv/install.ps1 | iex
    } catch {
        Stop-WithError "uv install failed: $_. Install manually from https://docs.astral.sh/uv/getting-started/installation/ and re-run."
    }
    # Astral's installer drops uv into ~/.local/bin and updates User PATH —
    # but that change doesn't apply to the CURRENT shell session. Force it.
    $uvBin = Join-Path $env:USERPROFILE ".local\bin"
    if (Test-Path $uvBin) { $env:Path = "$uvBin;$env:Path" }
    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
        Stop-WithError "uv install completed but the uv command is still not on PATH. Close + reopen PowerShell and re-run."
    }
    Write-StoaOk "uv OK"
}

# ─── Python via uv (no winget, no system-wide install) ───────────────

function Ensure-Python {
    # Let uv manage its own Python install. We do NOT call `python` from
    # PATH because the user may have an unrelated Python 2.7 / Microsoft
    # Store stub / partial 3.x install that breaks the rest of the flow.
    # `uv python install 3.13` is idempotent — if 3.13 is already in uv's
    # store, it returns instantly.
    Write-StoaInfo "Ensuring Python 3.13 (via uv)..."
    & uv python install 3.13 2>&1 | ForEach-Object { Write-StoaDim "    $_" }
    if ($LASTEXITCODE -ne 0) {
        Stop-WithError "uv could not install Python 3.13 (exit $LASTEXITCODE). Inspect the error above and re-run."
    }
    Write-StoaOk "Python 3.13 OK (managed by uv)"
}

# ─── git via PortableGit (no winget, no admin) ───────────────────────

function Get-PortableGitAsset {
    # Pinned git-for-windows release. We deliberately avoid api.github.com
    # /releases/latest because it's rate-limited to 60 req/hr per IP for
    # unauthenticated callers — users behind CGNAT / corporate NAT routinely
    # hit it and break the installer. Static asset URLs are NOT rate-limited.
    #
    # Audit P-M (install integrity): each pinned asset now also pins a known
    # SHA-256. After download we recompute the hash and refuse to extract if
    # it doesn't match. This protects against:
    #   * MITM downgrade attacks if a downstream CA is compromised
    #   * the (unlikely) case that git-for-windows force-pushes a tag and
    #     the asset bytes change underneath us
    #   * mirror poisoning (e.g. a corporate proxy that rewrites the file)
    #
    # SHA-256 values are taken from the git-for-windows release SHA256SUMS
    # block published on https://github.com/git-for-windows/git/releases.
    # When bumping $gitVer, regenerate via:
    #   curl -fsSL -o tmp.exe https://github.com/git-for-windows/git/releases/download/v<VER>.windows.1/<asset>
    #   Get-FileHash tmp.exe -Algorithm SHA256
    $gitVer = "2.54.0"
    $gitTag = "v$gitVer.windows.1"
    if ([Environment]::Is64BitOperatingSystem) {
        if ($env:PROCESSOR_ARCHITECTURE -eq "ARM64" -or $env:PROCESSOR_ARCHITEW6432 -eq "ARM64") {
            $asset = "PortableGit-$gitVer-arm64.7z.exe"
            # git-for-windows v2.54.0.windows.1 — SHA-256 from the GitHub
            # release asset digest (CF-7 / F-C17-001).
            $sha   = "f8e92cd3359fcbb96998cfd606a536ccc6dbfb23c04e12b29042f9ba45b6b0c7"
        } else {
            $asset = "PortableGit-$gitVer-64-bit.7z.exe"
            $sha   = "bea006a6cc69673f27b1647e84ab3a68e912fbc175ab6320c5987e012897f311"
        }
    } else {
        Write-StoaWarn "32-bit Windows detected. PortableGit is 64-bit only; using MinGit 32-bit as a last resort."
        Write-StoaWarn "Bash-dependent features (terminal tool, agent-browser) won't work on this box."
        $asset = "MinGit-$gitVer-32-bit.zip"
        $sha   = "52fc36c9b22611f0a6a7fabdc68c763b914400e3af0e35ad822468dc64cb7981"
    }
    return @{
        Url           = "https://github.com/git-for-windows/git/releases/download/$gitTag/$asset"
        Asset         = $asset
        IsZip         = ($asset -like "*.zip")
        ExpectedSha256 = $sha
    }
}

function Test-DownloadIntegrity {
    param(
        [string]$Path,
        [string]$ExpectedSha256,
        [string]$AssetName
    )
    # Empty $ExpectedSha256 means the constant hasn't been populated yet
    # (next release bump). We log a clear warning but do not block — refusing
    # to install on a half-populated installer would brick all users until
    # someone fills in the hash. STOA_REQUIRE_DOWNLOAD_HASH=1 flips this to
    # fail-closed for hardened environments.
    if ([string]::IsNullOrWhiteSpace($ExpectedSha256)) {
        $require = $env:STOA_REQUIRE_DOWNLOAD_HASH
        if ($require -eq "1" -or $require -eq "true") {
            Stop-WithError "STOA_REQUIRE_DOWNLOAD_HASH=1 and no expected SHA-256 is pinned for $AssetName. Refusing to extract an unverified download. Pin the hash in install.ps1 (Get-PortableGitAsset) and re-run."
        }
        Write-StoaWarn "No expected SHA-256 pinned for $AssetName — download integrity NOT verified."
        Write-StoaDim  "    Set STOA_REQUIRE_DOWNLOAD_HASH=1 to fail-closed instead."
        return
    }
    $actual = (Get-FileHash -Algorithm SHA256 -Path $Path).Hash.ToLowerInvariant()
    $expected = $ExpectedSha256.ToLowerInvariant()
    if ($actual -ne $expected) {
        Remove-Item $Path -ErrorAction SilentlyContinue
        Stop-WithError "SHA-256 mismatch for $AssetName. Expected $expected, got $actual. Aborting install — your download may have been tampered with or the upstream tag was moved."
    }
    Write-StoaOk "  Verified SHA-256 of $AssetName"
}

function Ensure-Git {
    if (Get-Command git -ErrorAction SilentlyContinue) {
        Write-StoaOk "git OK"
        return
    }
    $StoaGitDir = Join-Path $env:LOCALAPPDATA "stoa\git"
    if (Test-Path (Join-Path $StoaGitDir "cmd\git.exe")) {
        $env:Path = "$(Join-Path $StoaGitDir 'cmd');$(Join-Path $StoaGitDir 'usr\bin');$env:Path"
        if (Get-Command git -ErrorAction SilentlyContinue) {
            Write-StoaOk "git OK (PortableGit at $StoaGitDir)"
            return
        }
    }
    Write-StoaInfo "git not found - downloading PortableGit into $StoaGitDir ..."
    Write-StoaDim "(no admin rights, no winget dependency, no system Git interference)"
    $info = Get-PortableGitAsset
    $tmp = Join-Path $env:TEMP ("stoa-git-$([Guid]::NewGuid().ToString().Substring(0,8)).bin")
    try {
        Invoke-WebRequest -Uri $info.Url -OutFile $tmp -UseBasicParsing
    } catch {
        Stop-WithError @"
Failed to download PortableGit from
  $($info.Url)
Reason: $_

Manual fix:
  1. Install Git from https://git-scm.com/download/win
  2. Close + reopen PowerShell so PATH refreshes
  3. Re-run this installer
"@
    }
    # Audit P-M (install integrity): verify SHA-256 BEFORE extraction.
    # Extraction = arbitrary code execution (7z.exe self-extractor),
    # so the integrity gate must run between download and extract.
    Test-DownloadIntegrity -Path $tmp -ExpectedSha256 $info.ExpectedSha256 -AssetName $info.Asset

    if (-not (Test-Path $StoaGitDir)) {
        New-Item -ItemType Directory -Force -Path $StoaGitDir | Out-Null
    }
    if ($info.IsZip) {
        Expand-Archive -Path $tmp -DestinationPath $StoaGitDir -Force
    } else {
        # PortableGit ships as a self-extracting 7z.exe; -y suppresses prompts,
        # -gm2 is silent mode, -nr forbids auto-restart on partial extract.
        & $tmp -y -gm2 "-o$StoaGitDir" 2>&1 | ForEach-Object { Write-StoaDim "    $_" }
        if ($LASTEXITCODE -ne 0) {
            Stop-WithError "PortableGit extraction failed (exit $LASTEXITCODE). Try installing Git from https://git-scm.com/download/win and re-run."
        }
    }
    Remove-Item $tmp -ErrorAction SilentlyContinue
    $env:Path = "$(Join-Path $StoaGitDir 'cmd');$(Join-Path $StoaGitDir 'usr\bin');$env:Path"
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
        Stop-WithError "git command still not on PATH after PortableGit extraction. Close + reopen PowerShell and re-run."
    }
    Write-StoaOk "git OK (PortableGit at $StoaGitDir)"
}

# ─── main install ────────────────────────────────────────────────────

function Install-StoaAgent {
    Write-StoaBanner

    Write-StoaInfo "Checking prerequisites..."
    Ensure-Uv
    Ensure-Python
    Ensure-Git
    Write-Host ""

    $StoaRepo   = "https://github.com/STOAGENT/stoa-agent.git"
    $StoaHome   = if ($env:STOA_HOME)        { $env:STOA_HOME }        else { Join-Path $env:LOCALAPPDATA "stoa" }
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

    Write-StoaInfo "Creating venv + installing stoa-agent (via uv)..."
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

    # PATH wiring — drop a small launcher into a stable user-PATH dir.
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
    Write-StoaDim ""
    Write-StoaDim "  Uninstall = Remove-Item -Recurse $StoaHome  (no admin needed)"
    Write-Host ""
    Pause-IfInteractive "Press Enter to close"
}

try {
    Install-StoaAgent
} catch {
    # All Stop-WithError throws land here. The error is already printed +
    # paused — we swallow the exception so the script exits cleanly.
}
