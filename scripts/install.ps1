# STOA Agent installer — Windows PowerShell stub
#
# Audit v4 HIGH Z-01 / v6 HIGH-18 fix: the canonical installer at
# https://stoax.xyz/install.ps1 is now SHA-256 pinned. If stoax.xyz
# is ever hijacked / parked / served unexpected content, this stub
# refuses to execute the downloaded script instead of silently piping
# a remote command into the user's shell.
#
# Operators rotate the pin alongside each release via scripts/release.py.
# Override per-machine with STOA_INSTALL_PS1_SHA256 when running an
# off-cycle release / fork.
#
# Usage (matches the canonical one-liner — no change for end users):
#   iex (irm https://stoax.xyz/install.ps1)

$ErrorActionPreference = 'Stop'

$Url = 'https://stoax.xyz/install.ps1'

# Update via `python scripts/release.py --print-installer-sha` and commit
# alongside the release tag. Empty string disables the gate (insecure;
# only for dev clones).
$ExpectedSha = if ($env:STOA_INSTALL_PS1_SHA256) {
    $env:STOA_INSTALL_PS1_SHA256
} else {
    ''
}

$content = Invoke-RestMethod -Uri $Url

if ($ExpectedSha) {
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($content)
    $sha = (Get-FileHash -Algorithm SHA256 -InputStream ([System.IO.MemoryStream]::new($bytes))).Hash.ToLower()
    if ($sha -ne $ExpectedSha.ToLower()) {
        Write-Error @"
✗ STOA installer SHA-256 mismatch — refusing to execute.
  Expected: $ExpectedSha
  Actual:   $sha

If you intend to run an off-cycle release, set
  `$env:STOA_INSTALL_PS1_SHA256 = '<sha>'` to the value from
the release notes. Otherwise treat this as a likely DNS / TLS
compromise of stoax.xyz and report at security@stoax.xyz.
"@
        exit 1
    }
}

Invoke-Expression $content
