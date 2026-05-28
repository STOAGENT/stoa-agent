@echo off
REM ============================================================================
REM STOA Agent Installer for Windows (CMD wrapper)
REM ============================================================================
REM Bootstraps the PowerShell installer for users running CMD.
REM
REM Audit Phase-1A (PROBE-CRIT-006, install.cmd raw irm): the previous
REM wrapper piped `iex (irm ...)` against a mutable GitHub raw URL, which
REM gave any future change to install.ps1 (legitimate or otherwise)
REM immediate, unverified execution on every fresh Windows install.
REM
REM The new flow:
REM   1. Download install.ps1 to a temp file via Invoke-WebRequest
REM      (which honours system TLS + system root CA list, unlike `irm`
REM      whose error surface is harder to read).
REM   2. If STOA_INSTALL_SHA256 is set in the environment, verify the
REM      downloaded file's SHA-256 matches it BEFORE execution. The
REM      env var is the integrity channel users can pass in from a
REM      release-page sticker.
REM   3. Invoke the local file with explicit execution policy bypass
REM      (same as before, but operating on a file we just verified).
REM
REM Usage:
REM   set STOA_INSTALL_SHA256=<sha from release page>     (optional, recommended)
REM   curl -fsSL https://raw.githubusercontent.com/STOAGENT/stoa-agent/master/scripts/install.cmd -o install.cmd
REM   install.cmd
REM
REM Or use the PowerShell-native bootstrap if you're already in PS:
REM   $env:STOA_INSTALL_SHA256 = '<sha>'
REM   Invoke-WebRequest -Uri https://raw.githubusercontent.com/STOAGENT/stoa-agent/master/scripts/install.ps1 -OutFile $env:TEMP\stoa-install.ps1
REM   Get-FileHash $env:TEMP\stoa-install.ps1   # compare with sticker
REM   powershell -ExecutionPolicy Bypass -File $env:TEMP\stoa-install.ps1
REM ============================================================================

setlocal enabledelayedexpansion

echo.
echo  STOA Agent Installer
echo  Downloading install.ps1...
echo.

set "STOA_INSTALL_TMP=%TEMP%\stoa-install-%RANDOM%.ps1"

powershell -ExecutionPolicy ByPass -NoProfile -Command ^
    "$ProgressPreference = 'SilentlyContinue'; Invoke-WebRequest -Uri 'https://raw.githubusercontent.com/STOAGENT/stoa-agent/master/scripts/install.ps1' -OutFile '%STOA_INSTALL_TMP%' -UseBasicParsing"

if not exist "%STOA_INSTALL_TMP%" (
    echo.
    echo  Download failed. Check your internet connection or download manually:
    echo    https://github.com/STOAGENT/stoa-agent/blob/master/scripts/install.ps1
    echo.
    pause
    exit /b 1
)

if defined STOA_INSTALL_SHA256 (
    echo  Verifying SHA-256...
    for /f "tokens=*" %%H in ('powershell -NoProfile -Command "(Get-FileHash -Algorithm SHA256 '%STOA_INSTALL_TMP%').Hash.ToLower()"') do set "ACTUAL_SHA=%%H"
    set "EXPECTED_SHA=%STOA_INSTALL_SHA256%"
    if /I not "!ACTUAL_SHA!"=="%EXPECTED_SHA%" (
        echo.
        echo  SHA-256 mismatch.
        echo    expected: %EXPECTED_SHA%
        echo    got:      !ACTUAL_SHA!
        echo  Refusing to run an unverified installer.
        del "%STOA_INSTALL_TMP%" >nul 2>&1
        pause
        exit /b 1
    )
    echo  SHA-256 verified.
)

echo  Launching installer...
powershell -ExecutionPolicy ByPass -NoProfile -File "%STOA_INSTALL_TMP%"
set "INSTALL_RC=%ERRORLEVEL%"

del "%STOA_INSTALL_TMP%" >nul 2>&1

if not "%INSTALL_RC%"=="0" (
    echo.
    echo  Installation failed (exit %INSTALL_RC%). For manual install see:
    echo    https://github.com/STOAGENT/stoa-agent#install
    echo.
    pause
    exit /b %INSTALL_RC%
)

exit /b 0
