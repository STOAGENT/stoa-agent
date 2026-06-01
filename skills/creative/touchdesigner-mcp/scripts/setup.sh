#!/usr/bin/env bash
# setup.sh — Automated setup for twozero MCP plugin for TouchDesigner
# Idempotent: safe to run multiple times.
set -euo pipefail

GREEN='\033[0;32m'; RED='\033[0;31m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
OK="${GREEN}✔${NC}"; FAIL="${RED}✘${NC}"; WARN="${YELLOW}⚠${NC}"

TWOZERO_URL="https://www.404zero.com/pisang/twozero.tox"
TOX_PATH="$HOME/Downloads/twozero.tox"
STOA_HOME_DIR="${STOA_HOME:-$HOME/.stoa}"
STOA_CFG="${STOA_HOME_DIR}/config.yaml"
MCP_PORT=40404
MCP_ENDPOINT="http://localhost:${MCP_PORT}/mcp"

manual_steps=()

echo -e "\n${CYAN}═══ twozero MCP for TouchDesigner — Setup ═══${NC}\n"

# ── 1. Check if TouchDesigner is running ──
# Match on process *name* (not full cmdline) to avoid self-matching shells
# that happen to have "TouchDesigner" in their args. macOS and Linux pgrep
# both support -x for exact name match.
if pgrep -x TouchDesigner >/dev/null 2>&1 || pgrep -x TouchDesignerFTE >/dev/null 2>&1; then
    echo -e " ${OK} TouchDesigner is running"
    td_running=true
else
    echo -e " ${WARN} TouchDesigner is not running"
    td_running=false
fi

# ── 2. Ensure twozero.tox exists ──
# Set TWOZERO_SHA256 to the SHA-256 you independently obtained from a trusted
# source (the 404zero release page / maintainer). When set, the download is
# verified against it and the script aborts on mismatch. When empty, the
# checksum is only PRINTED for you to verify manually — it is NOT trusted.
TWOZERO_SHA256="${TWOZERO_SHA256:-}"

sha256_of() {
    # Cross-platform SHA-256 of a file → lowercase hex on stdout (empty on failure)
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$1" 2>/dev/null | awk '{print $1}'
    elif command -v shasum >/dev/null 2>&1; then
        shasum -a 256 "$1" 2>/dev/null | awk '{print $1}'
    else
        echo ""
    fi
}

if [[ -f "$TOX_PATH" ]]; then
    echo -e " ${OK} twozero.tox already exists at ${TOX_PATH}"
else
    echo -e " ${WARN} twozero.tox not found — downloading..."
    if curl -fSL -o "$TOX_PATH" "$TWOZERO_URL" 2>/dev/null; then
        echo -e " ${OK} Downloaded twozero.tox to ${TOX_PATH}"
    else
        echo -e " ${FAIL} Failed to download twozero.tox from ${TWOZERO_URL}"
        echo "       Please download manually and place at ${TOX_PATH}"
        manual_steps+=("Download twozero.tox from ${TWOZERO_URL} to ${TOX_PATH}")
    fi
fi

# ── 2b. Verify the downloaded .tox (SHA-256) ──
if [[ -f "$TOX_PATH" ]]; then
    actual_sha="$(sha256_of "$TOX_PATH")"
    if [[ -z "$actual_sha" ]]; then
        echo -e " ${WARN} Cannot compute SHA-256 (no sha256sum/shasum). VERIFY ${TOX_PATH} MANUALLY before use."
        manual_steps+=("Manually verify the SHA-256 / provenance of ${TOX_PATH} before opening it in TouchDesigner")
    elif [[ -n "$TWOZERO_SHA256" ]]; then
        if [[ "$actual_sha" == "$TWOZERO_SHA256" ]]; then
            echo -e " ${OK} twozero.tox SHA-256 matches the expected value"
        else
            echo -e " ${FAIL} twozero.tox SHA-256 MISMATCH — refusing to proceed."
            echo "       expected: ${TWOZERO_SHA256}"
            echo "       actual:   ${actual_sha}"
            echo "       The download may be corrupted or tampered. Delete it and re-verify the source."
            exit 1
        fi
    else
        echo -e " ${WARN} No TWOZERO_SHA256 pinned. A .tox is executable content for TouchDesigner."
        echo "       Downloaded SHA-256: ${actual_sha}"
        echo "       VERIFY this against the maintainer's published hash before opening it in TD,"
        echo "       then re-run with TWOZERO_SHA256=<that-hash> to enforce it."
        manual_steps+=("Verify ${TOX_PATH} (SHA-256 ${actual_sha}) against the maintainer's published hash before use")
    fi
fi

# ── 3. STOA config: PRINT the twozero_td MCP stanza for the user to add manually ──
# We deliberately do NOT auto-write mcp_servers. Registering an MCP peer grants it
# tool-providing access to the agent on every future session, and the endpoint is
# whatever is listening on localhost:${MCP_PORT}. The user must add it consciously.
if [[ -f "$STOA_CFG" ]] && grep -q 'twozero_td' "$STOA_CFG" 2>/dev/null; then
    echo -e " ${OK} twozero_td MCP entry already exists in STOA config"
else
    echo -e " ${WARN} STOA config will NOT be modified automatically."
    echo "       Review the stanza below, then add it under 'mcp_servers:' in ${STOA_CFG} yourself."
    echo "       Only add it if you trust whatever is serving ${MCP_ENDPOINT}."
    echo ""
    echo "  mcp_servers:"
    echo "    twozero_td:"
    echo "      url: \"${MCP_ENDPOINT}\""
    echo "      timeout: 120"
    echo "      connect_timeout: 60"
    echo ""
    manual_steps+=("Add the twozero_td 'mcp_servers' stanza (printed above) to ${STOA_CFG} manually")
    manual_steps+=("Restart STOA session to pick up the config change")
fi

# ── 4. Test if MCP port is responding ──
if nc -z 127.0.0.1 "$MCP_PORT" 2>/dev/null; then
    echo -e " ${OK} Port ${MCP_PORT} is open"

    # ── 5. Verify MCP endpoint responds ──
    resp=$(curl -s --max-time 3 "$MCP_ENDPOINT" 2>/dev/null || true)
    if [[ -n "$resp" ]]; then
        echo -e " ${OK} MCP endpoint responded at ${MCP_ENDPOINT}"
    else
        echo -e " ${WARN} Port open but MCP endpoint returned empty response"
        manual_steps+=("Verify MCP is enabled in twozero settings")
    fi
else
    echo -e " ${WARN} Port ${MCP_PORT} is not open"
    if [[ "$td_running" == true ]]; then
        manual_steps+=("In TD: drag twozero.tox into network editor → click Install")
        manual_steps+=("Enable MCP: twozero icon → Settings → mcp → 'auto start MCP' → Yes")
    else
        manual_steps+=("Launch TouchDesigner")
        manual_steps+=("Drag twozero.tox into the TD network editor and click Install")
        manual_steps+=("Enable MCP: twozero icon → Settings → mcp → 'auto start MCP' → Yes")
    fi
fi

# ── Status Report ──
echo -e "\n${CYAN}═══ Status Report ═══${NC}\n"

if [[ ${#manual_steps[@]} -eq 0 ]]; then
    echo -e " ${OK} ${GREEN}Fully configured! twozero MCP is ready to use.${NC}\n"
    exit 0
else
    echo -e " ${WARN} ${YELLOW}Manual steps remaining:${NC}\n"
    for i in "${!manual_steps[@]}"; do
        echo -e "   $((i+1)). ${manual_steps[$i]}"
    done
    echo ""
    exit 1
fi
