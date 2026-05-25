"""Shared file safety rules used by both tools and ACP shims."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional


def _stoa_home_path() -> Path:
    """Resolve the active STOA_HOME (profile-aware) without circular imports."""
    try:
        from stoa_constants import get_stoa_home  # local import to avoid cycles
        return get_stoa_home()
    except Exception:
        return Path(os.path.expanduser("~/.stoa"))


def _stoa_root_path() -> Path:
    """Resolve the STOA root dir (always the parent of any profile, never per-profile)."""
    try:
        from stoa_constants import get_default_stoa_root  # local import to avoid cycles
        return get_default_stoa_root()
    except Exception:
        return Path(os.path.expanduser("~/.stoa"))


def build_write_denied_paths(home: str) -> set[str]:
    """Return exact sensitive paths that must never be written."""
    stoa_home = _stoa_home_path()
    stoa_root = _stoa_root_path()
    return {
        os.path.realpath(p)
        for p in [
            os.path.join(home, ".ssh", "authorized_keys"),
            os.path.join(home, ".ssh", "id_rsa"),
            os.path.join(home, ".ssh", "id_ed25519"),
            os.path.join(home, ".ssh", "config"),
            # Active profile .env (or top-level .env when not in profile mode).
            str(stoa_home / ".env"),
            # Top-level .env, even when running under a profile — overwriting it
            # leaks credentials across every profile that inherits from root (#15981).
            str(stoa_root / ".env"),
            os.path.join(home, ".bashrc"),
            os.path.join(home, ".zshrc"),
            os.path.join(home, ".profile"),
            os.path.join(home, ".bash_profile"),
            os.path.join(home, ".zprofile"),
            os.path.join(home, ".netrc"),
            os.path.join(home, ".pgpass"),
            os.path.join(home, ".npmrc"),
            os.path.join(home, ".pypirc"),
            "/etc/sudoers",
            "/etc/passwd",
            "/etc/shadow",
        ]
    }


def build_write_denied_prefixes(home: str) -> list[str]:
    """Return sensitive directory prefixes that must never be written."""
    return [
        os.path.realpath(p) + os.sep
        for p in [
            os.path.join(home, ".ssh"),
            os.path.join(home, ".aws"),
            os.path.join(home, ".gnupg"),
            os.path.join(home, ".kube"),
            "/etc/sudoers.d",
            "/etc/systemd",
            os.path.join(home, ".docker"),
            os.path.join(home, ".azure"),
            os.path.join(home, ".config", "gh"),
        ]
    ]


def get_safe_write_root() -> Optional[str]:
    """Return the resolved STOA_WRITE_SAFE_ROOT path, or None if unset."""
    root = os.getenv("STOA_WRITE_SAFE_ROOT", "")
    if not root:
        return None
    try:
        return os.path.realpath(os.path.expanduser(root))
    except Exception:
        return None


def is_write_denied(path: str) -> bool:
    """Return True if path is blocked by the write denylist or safe root."""
    home = os.path.realpath(os.path.expanduser("~"))
    resolved = os.path.realpath(os.path.expanduser(str(path)))

    if resolved in build_write_denied_paths(home):
        return True
    for prefix in build_write_denied_prefixes(home):
        if resolved.startswith(prefix):
            return True

    # STOA control-plane files: block both the ACTIVE profile's view
    # (stoa_home) AND the global root view. Without the root pass, a
    # profile-mode session leaves <root>/auth.json + <root>/config.yaml
    # writable — letting a prompt-injected write_file overwrite the global
    # files that every profile inherits from (same shape as #15981).
    control_file_names = ("auth.json", "config.yaml", "webhook_subscriptions.json")
    mcp_tokens_dir_name = "mcp-tokens"

    stoa_dirs = []
    for base in (_stoa_home_path(), _stoa_root_path()):
        try:
            real = os.path.realpath(base)
            if real not in stoa_dirs:
                stoa_dirs.append(real)
        except Exception:
            continue

    for base_real in stoa_dirs:
        for name in control_file_names:
            try:
                if resolved == os.path.realpath(os.path.join(base_real, name)):
                    return True
            except Exception:
                continue
        try:
            mcp_real = os.path.realpath(os.path.join(base_real, mcp_tokens_dir_name))
            if resolved == mcp_real or resolved.startswith(mcp_real + os.sep):
                return True
        except Exception:
            pass

    safe_root = get_safe_write_root()
    if safe_root and not (resolved == safe_root or resolved.startswith(safe_root + os.sep)):
        return True

    return False


def get_read_block_error(path: str) -> Optional[str]:
    """Return an error message when a read targets a denied STOA path.

    Two categories are blocked:

      * Internal STOA cache files under ``STOA_HOME/skills/.hub`` —
        readable metadata that an attacker could use as a prompt-injection
        carrier.
      * Credential / secret stores under STOA_HOME and the global STOA
        root: ``auth.json``, ``auth.lock``, ``.anthropic_oauth.json``,
        ``.env``, ``webhook_subscriptions.json``, and anything under
        ``mcp-tokens/``. These hold plaintext provider keys, OAuth
        tokens, and HMAC secrets that the agent never needs to read
        directly — provider tools and gateway adapters consume them
        through internal channels.

    **This is NOT a security boundary.** The terminal tool runs as the
    same OS user with shell access; the agent can still ``cat auth.json``
    or ``cat ~/.stoa/.env`` and exfiltrate the file. The read-deny exists
    as defense-in-depth that:

      * Returns a clear error to models that respect tool denials, which
        empirically prompts most modern models to stop rather than reach
        for the shell.
      * Surfaces a visible audit trail when something tries to read
        credentials — easier to spot in logs than a generic ``cat``.

    Treat any user-visible framing around this as "may help" rather than
    "stops attackers." A determined model or malicious instruction can
    always shell out.

    Callers that resolve relative paths against a non-process cwd
    (e.g. ``TERMINAL_CWD`` in ``tools/file_tools.py``) MUST pre-resolve
    and pass the absolute path string.  This function's own ``resolve()``
    is anchored at the Python process cwd, so a relative input like
    ``"auth.json"`` would otherwise miss the denylist when the task's
    terminal cwd differs from the process cwd.
    """
    resolved = Path(path).expanduser().resolve()

    # Resolve BOTH the active STOA_HOME (profile-aware) AND the global
    # STOA root so credential stores at <root>/auth.json etc. are also
    # blocked when running under a profile (STOA_HOME points at
    # <root>/profiles/<name> in profile mode). Same shape as the write
    # deny widening (#15981, #14157).
    stoa_dirs: list[Path] = []
    for base in (_stoa_home_path(), _stoa_root_path()):
        try:
            real = base.resolve()
            if real not in stoa_dirs:
                stoa_dirs.append(real)
        except Exception:
            continue

    # Skills .hub: prompt-injection carriers.
    for hd in stoa_dirs:
        blocked_dirs = [
            hd / "skills" / ".hub" / "index-cache",
            hd / "skills" / ".hub",
        ]
        for blocked in blocked_dirs:
            try:
                resolved.relative_to(blocked)
            except ValueError:
                continue
            return (
                f"Access denied: {path} is an internal STOA cache file "
                "and cannot be read directly to prevent prompt injection. "
                "Use the skills_list or skill_view tools instead."
            )

    # Credential / secret stores. Exact-file matches under either
    # STOA_HOME or <root>.
    credential_file_names = (
        "auth.json",
        "auth.lock",
        ".anthropic_oauth.json",
        ".env",
        "webhook_subscriptions.json",
    )
    for hd in stoa_dirs:
        for name in credential_file_names:
            try:
                blocked = (hd / name).resolve()
            except Exception:
                continue
            if resolved == blocked:
                return (
                    f"Access denied: {path} is a STOA credential store "
                    "and cannot be read directly. Provider tools consume "
                    "these credentials through internal channels. "
                    "(Defense-in-depth — not a security boundary; the "
                    "terminal tool can still bypass.)"
                )

    # mcp-tokens/: directory prefix match — anything inside is OAuth
    # token material.
    for hd in stoa_dirs:
        try:
            mcp_tokens = (hd / "mcp-tokens").resolve()
        except Exception:
            continue
        if resolved == mcp_tokens:
            return (
                f"Access denied: {path} is the STOA MCP token directory "
                "and cannot be read directly. (Defense-in-depth — not a "
                "security boundary; the terminal tool can still bypass.)"
            )
        try:
            resolved.relative_to(mcp_tokens)
        except ValueError:
            continue
        return (
            f"Access denied: {path} is a STOA MCP token file "
            "and cannot be read directly. (Defense-in-depth — not a "
            "security boundary; the terminal tool can still bypass.)"
        )

    # Audit v5 CRIT T-01: user-level credential stores.
    # The read-deny was previously STOA-only; this missed every popular
    # cloud-credential store on disk (~/.ssh, ~/.aws, ~/.gnupg, ~/.kube,
    # /etc/shadow, etc.). The same defense-in-depth justification applies:
    # models that respect tool denials stop here, and even when they reach
    # for the shell the visible refusal lands in audit logs.
    home = Path(os.path.expanduser("~")).resolve()
    user_secret_files = (
        home / ".netrc",
        home / ".pgpass",
        home / ".npmrc",
        home / ".pypirc",
        home / ".gitconfig",
        Path("/etc/shadow"),
        Path("/etc/sudoers"),
    )
    for blocked in user_secret_files:
        try:
            if resolved == blocked.resolve(strict=False):
                return (
                    f"Access denied: {path} is a user-level credential / "
                    "system secret file. (Defense-in-depth — not a security "
                    "boundary; the terminal tool can still bypass.)"
                )
        except Exception:
            continue

    user_secret_dirs = (
        home / ".ssh",
        home / ".aws",
        home / ".gnupg",
        home / ".kube",
        home / ".docker",
        home / ".azure",
        home / ".config" / "gh",
        home / ".gcloud",
        home / ".config" / "gcloud",
        Path("/etc/sudoers.d"),
    )
    for d in user_secret_dirs:
        try:
            d_real = d.resolve(strict=False)
        except Exception:
            continue
        if resolved == d_real:
            return (
                f"Access denied: {path} is a user-level credential directory "
                "and may contain private keys / tokens. (Defense-in-depth — "
                "not a security boundary; the terminal tool can still bypass.)"
            )
        try:
            resolved.relative_to(d_real)
        except ValueError:
            continue
        return (
            f"Access denied: {path} sits inside a user-level credential "
            "directory ({d_real}). Provider tools consume these credentials "
            "through internal channels. (Defense-in-depth — not a security "
            "boundary; the terminal tool can still bypass.)"
        )

    # /proc/<pid>/environ leaks every env var of any process the user can
    # read — including STOA's own credentials. Block reads regardless of pid.
    # On non-Linux hosts /proc/ doesn't exist as a real path, so we match
    # against BOTH the resolved form and the original path string (the
    # caller may pass /proc/1/environ verbatim on a Linux system without
    # going through expanduser).
    for candidate_str in (str(resolved).replace("\\", "/"), str(path).replace("\\", "/")):
        if candidate_str.startswith("/proc/") and candidate_str.endswith("/environ"):
            return (
                f"Access denied: {path} exposes process environment variables "
                "which routinely contain credentials. (Defense-in-depth — not a "
                "security boundary; the terminal tool can still bypass.)"
            )

    return None
