#!/usr/bin/env python3
"""
Skills Signature — opt-in ed25519 signature verification, revocation manifest,
author identity binding, and push-update diff classification for the
Skills Hub.

Audit v11 HIGH-61 mitigation (7 findings):

  1. Bundle signature ABSENT — TRUSTED_REPOS is a plain string-match list.
     Anyone who can publish into a forked repo with a matching slug, or
     who can MITM the GitHub Contents API response (CDN poisoning, DNS
     hijack), can ship a malicious bundle that scans clean.
  2. ``author:`` frontmatter is unvalidated — ``author: anthropic`` can
     be typed by any author, so a community skill can typosquat a
     trusted vendor in the UI and in the lock file.
  3. No revocation kill-switch. The 6-hour TTL on the centralized index
     means a known-bad skill stays installable for up to six hours after
     publish.
  4. Author push-update is silent — a published skill that previously
     had three files can have ``scripts/exfil.sh`` added on the next
     upstream push without any user re-prompt.
  5. ``stoa skill publish`` against arbitrary repos uses the local
     GITHUB_TOKEN — an installed skill can push to repos that don't
     belong to the user's own ``github:<org>`` identity.
  6. The Trust-of-First-Use flow on the marketplace publish gate is a
     facade — no signed first-publish anchor is stored, so a
     compromised maintainer key replays the entire trust chain.
  7. No version pinning at install time — a skill installed at v1.0.0
     can be silently upgraded to a malicious v1.0.1 on the next
     ``check_for_skill_updates`` pass without consent.

Design constraints (mandatory from the audit ticket):

  - **Default OFF.** ``STOA_SKILL_REQUIRE_SIGNATURE`` unset OR != "1"
    means the existing TRUSTED_REPOS string-match path is preserved
    byte-for-byte. No behaviour change for current users.
  - **Backward-compat.** Signature files are optional unless the env
    flag is on. Bundles that ship without a signature continue to work
    in default mode.
  - **Opt-in.** Power users / operators flip the env flag and from
    that point onward installs require a valid signature, the bundle
    sha is not in the revocation manifest, and the ``author:`` field
    binds to a registered identity prefix.

Public API:

    is_signature_required()          -> bool
    verify_bundle_signature(...)      -> SignatureVerdict
    is_sha_revoked(content_hash)      -> RevocationVerdict
    validate_author_field(...)        -> AuthorVerdict
    classify_update_diff(...)         -> UpdateDiffVerdict
    check_publish_target_repo(...)    -> TargetRepoVerdict

Each *Verdict* is a tuple ``(ok: bool | None, reason: str, detail: dict)``
where ``None`` means "needs interactive confirmation" so callers can
mirror the existing ``should_allow_install`` ternary contract.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from stoa_constants import get_stoa_home


# ---------------------------------------------------------------------------
# Paths and env flags
# ---------------------------------------------------------------------------

STOA_HOME = get_stoa_home()
HUB_DIR = STOA_HOME / "skills" / ".hub"
REVOCATIONS_FILE = HUB_DIR / "revocations.json"
TRUSTED_SIGNERS_FILE = HUB_DIR / "trusted-signers.json"

SIGNATURE_FILENAME = ".signature.json"

ENV_REQUIRE_SIGNATURE = "STOA_SKILL_REQUIRE_SIGNATURE"


def is_signature_required() -> bool:
    """Return True iff the strict signature mode is enabled.

    Default OFF.  Existing TRUSTED_REPOS behaviour is preserved when this
    returns False — the rest of this module short-circuits to "allow,
    legacy mode" so installers don't need to fork their happy path.
    """
    return os.environ.get(ENV_REQUIRE_SIGNATURE, "").strip() == "1"


# ---------------------------------------------------------------------------
# Verdict data classes
# ---------------------------------------------------------------------------

@dataclass
class SignatureVerdict:
    ok: Optional[bool]              # True / False / None ("needs confirmation")
    reason: str
    signer_pubkey_hex: str = ""
    detail: Dict[str, object] = field(default_factory=dict)


@dataclass
class RevocationVerdict:
    ok: Optional[bool]              # False if revoked, True otherwise
    reason: str
    revoked_at_ms: Optional[int] = None
    revocation_reason: str = ""


@dataclass
class AuthorVerdict:
    ok: bool
    reason: str
    canonical_identity: str = ""    # e.g. "github:anthropics" or "did:web:anthropic.com"


@dataclass
class UpdateDiffVerdict:
    classification: str             # "silent" | "warn" | "block"
    reason: str
    added_files: List[str] = field(default_factory=list)
    removed_files: List[str] = field(default_factory=list)
    modified_files: List[str] = field(default_factory=list)


@dataclass
class TargetRepoVerdict:
    ok: bool
    reason: str
    declared_identity: str = ""
    target_org: str = ""


# ---------------------------------------------------------------------------
# Ed25519 signature verification
# ---------------------------------------------------------------------------
#
# Signature file format (``<bundle>/.signature.json``):
#
#   {
#       "scheme": "ed25519-v1",
#       "signer_pubkey": "<hex 64 chars>",      # 32-byte raw ed25519 pubkey
#       "content_hash": "sha256:<64 hex>",       # MUST match bundle hash
#       "signature":    "<hex 128 chars>",       # detached signature over
#                                                # content_hash UTF-8 bytes
#       "signed_at_ms": <int unix-ms, optional>,
#       "identity":     "github:<org> | did:web:<host>"  (optional, advisory)
#   }
#
# The signed payload is the literal UTF-8 of the ``content_hash`` string
# (e.g. ``b"sha256:abc..."``).  This binds the signature 1:1 to the
# canonical content hash computed by ``skills_guard.content_hash`` /
# ``skills_hub.bundle_content_hash`` and lets a verifier check a bundle
# without re-hashing every file as long as the content hash is supplied.

def verify_bundle_signature(
    skill_dir: Path,
    expected_content_hash: str,
    *,
    trusted_signers: Optional[Iterable[str]] = None,
) -> SignatureVerdict:
    """Verify the detached ed25519 signature next to a quarantined bundle.

    Behaviour matrix:

      * signature mode OFF                        → ok=True (legacy path)
      * mode ON + signature file missing          → ok=False ("REJECT")
      * mode ON + malformed signature             → ok=False
      * mode ON + content_hash mismatch           → ok=False
      * mode ON + crypto verify fails             → ok=False
      * mode ON + verify OK + signer not in       → ok=None ("needs
        ``trusted_signers``                          confirmation")
      * mode ON + verify OK + signer trusted      → ok=True
    """
    if not is_signature_required():
        return SignatureVerdict(
            ok=True,
            reason="signature-mode-off (legacy TRUSTED_REPOS path)",
        )

    sig_path = skill_dir / SIGNATURE_FILENAME
    if not sig_path.is_file():
        return SignatureVerdict(
            ok=False,
            reason=(
                "STOA_SKILL_REQUIRE_SIGNATURE=1 but "
                f"{SIGNATURE_FILENAME} is missing from the bundle"
            ),
        )

    try:
        sig_doc = json.loads(sig_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return SignatureVerdict(
            ok=False,
            reason=f"signature file unreadable: {exc}",
        )

    if not isinstance(sig_doc, dict):
        return SignatureVerdict(ok=False, reason="signature file is not a JSON object")

    scheme = sig_doc.get("scheme")
    if scheme != "ed25519-v1":
        return SignatureVerdict(
            ok=False,
            reason=f"unsupported signature scheme: {scheme!r}",
        )

    signer_hex = sig_doc.get("signer_pubkey", "")
    sig_hex = sig_doc.get("signature", "")
    claimed_hash = sig_doc.get("content_hash", "")

    if not (isinstance(signer_hex, str) and re.fullmatch(r"[0-9a-fA-F]{64}", signer_hex)):
        return SignatureVerdict(ok=False, reason="signer_pubkey must be 64 hex chars")
    if not (isinstance(sig_hex, str) and re.fullmatch(r"[0-9a-fA-F]{128}", sig_hex)):
        return SignatureVerdict(ok=False, reason="signature must be 128 hex chars")
    if not isinstance(claimed_hash, str):
        return SignatureVerdict(ok=False, reason="content_hash missing from signature")

    if claimed_hash != expected_content_hash:
        return SignatureVerdict(
            ok=False,
            reason=(
                "content_hash mismatch — bundle hash and signed hash diverge "
                "(possible tamper)"
            ),
            detail={"bundle_hash": expected_content_hash, "signed_hash": claimed_hash},
        )

    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PublicKey,
        )
        from cryptography.exceptions import InvalidSignature
    except ImportError:
        return SignatureVerdict(
            ok=False,
            reason="`cryptography` package not installed — cannot verify ed25519",
        )

    try:
        pubkey = Ed25519PublicKey.from_public_bytes(bytes.fromhex(signer_hex))
        pubkey.verify(bytes.fromhex(sig_hex), claimed_hash.encode("utf-8"))
    except InvalidSignature:
        return SignatureVerdict(
            ok=False,
            reason="ed25519 signature verification failed",
            signer_pubkey_hex=signer_hex,
        )
    except (ValueError, TypeError) as exc:
        return SignatureVerdict(
            ok=False,
            reason=f"signature decode error: {exc}",
        )

    signers = set(trusted_signers) if trusted_signers is not None else _load_trusted_signers()
    if signer_hex.lower() not in {s.lower() for s in signers}:
        return SignatureVerdict(
            ok=None,
            reason=(
                "signature is valid but signer pubkey is not in the "
                "trusted-signers manifest — needs user confirmation (TOFU)"
            ),
            signer_pubkey_hex=signer_hex,
            detail={"identity": sig_doc.get("identity", "")},
        )

    return SignatureVerdict(
        ok=True,
        reason="ed25519 signature verified against trusted signer",
        signer_pubkey_hex=signer_hex,
        detail={"identity": sig_doc.get("identity", "")},
    )


def _load_trusted_signers() -> List[str]:
    """Load the trusted-signers manifest (list of hex pubkeys)."""
    if not TRUSTED_SIGNERS_FILE.exists():
        return []
    try:
        data = json.loads(TRUSTED_SIGNERS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if isinstance(data, dict):
        signers = data.get("signers", [])
    elif isinstance(data, list):
        signers = data
    else:
        signers = []
    return [s for s in signers if isinstance(s, str)]


def sign_bundle(
    content_hash: str,
    private_key_hex: str,
    *,
    identity: str = "",
) -> Dict[str, object]:
    """Produce a signature document for the given content hash.

    Helper for tests and the (future) ``stoa skill publish`` CLI. The
    return value is the dict that should be serialised to the bundle's
    ``.signature.json`` file.
    """
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey,
    )

    priv = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(private_key_hex))
    pub_bytes = priv.public_key().public_bytes_raw()
    sig = priv.sign(content_hash.encode("utf-8"))

    doc: Dict[str, object] = {
        "scheme": "ed25519-v1",
        "signer_pubkey": pub_bytes.hex(),
        "content_hash": content_hash,
        "signature": sig.hex(),
        "signed_at_ms": int(time.time() * 1000),
    }
    if identity:
        doc["identity"] = identity
    return doc


# ---------------------------------------------------------------------------
# Revocation manifest
# ---------------------------------------------------------------------------
#
# ``~/.stoa/skills/.hub/revocations.json`` schema:
#
#   {
#       "version": 1,
#       "revoked": {
#            "sha256:<hex>": {
#                "revoked_at_ms": <int>,
#                "reason": "<string>",
#                "skill_name": "<string, optional>"
#            },
#            ...
#       }
#   }
#
# The manifest is consulted before install (both fresh install and the
# upgrade path).  A revoked content hash blocks the install regardless
# of the trust level or signature validity — revocation is the
# kill-switch that bypasses the 6h index TTL.

def _load_revocations() -> Dict[str, dict]:
    if not REVOCATIONS_FILE.exists():
        return {}
    try:
        data = json.loads(REVOCATIONS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    revoked = data.get("revoked", {}) if isinstance(data, dict) else {}
    return revoked if isinstance(revoked, dict) else {}


def _save_revocations(revoked: Dict[str, dict]) -> None:
    REVOCATIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = {"version": 1, "revoked": revoked}
    REVOCATIONS_FILE.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def is_sha_revoked(content_hash: str) -> RevocationVerdict:
    """Return ``ok=False`` if the bundle's content hash is in the revocation manifest.

    This check runs UNCONDITIONALLY (no env-flag gate) — once the
    operator has added a sha to ``revocations.json`` the kill-switch
    fires even in legacy mode.  The whole point of the manifest is to
    bypass the 6h cache TTL when a known-bad skill is identified.
    """
    revoked = _load_revocations()
    entry = revoked.get(content_hash)
    if entry is None:
        return RevocationVerdict(ok=True, reason="not in revocation manifest")

    return RevocationVerdict(
        ok=False,
        reason=(
            f"content hash {content_hash} is REVOKED — "
            f"reason: {entry.get('reason', 'unspecified')}"
        ),
        revoked_at_ms=int(entry.get("revoked_at_ms", 0) or 0) or None,
        revocation_reason=str(entry.get("reason", "")),
    )


def revoke_sha(
    content_hash: str,
    reason: str,
    *,
    skill_name: str = "",
) -> None:
    """Helper for CI / operators to add a sha to the revocation manifest."""
    revoked = _load_revocations()
    revoked[content_hash] = {
        "revoked_at_ms": int(time.time() * 1000),
        "reason": reason,
        "skill_name": skill_name,
    }
    _save_revocations(revoked)


# ---------------------------------------------------------------------------
# Author identity binding
# ---------------------------------------------------------------------------
#
# Audit v11 HIGH-61 finding #2: the ``author:`` field in SKILL.md
# frontmatter is a free-form string.  ``author: anthropic`` typed by a
# community contributor is indistinguishable in the UI and lock file
# from ``author: anthropic`` typed by an actual Anthropic maintainer.
#
# Mitigation:  when the strict signature mode is ON, the author field
# MUST match one of two canonical prefix forms:
#
#     github:<org>                 e.g. github:anthropics
#     did:web:<host>               e.g. did:web:anthropic.com
#
# Both are checked against a regex; the host/org segment must be ASCII
# alphanumeric + ``-_.`` to defeat homoglyph attacks.  A small built-in
# "known-imposter" list catches the obvious typosquats
# (``anthropic`` vs ``anthropics``, etc.).

AUTHOR_GITHUB_RE = re.compile(r"^github:([A-Za-z0-9](?:[A-Za-z0-9\-]{0,38}[A-Za-z0-9])?)$")
AUTHOR_DID_WEB_RE = re.compile(r"^did:web:([a-z0-9](?:[a-z0-9\-\.]{1,253}[a-z0-9]))$")

# Imposter lookup: (typosquat -> canonical legitimate identity).
# A community publisher cannot use any of the LHS values; we explicitly
# reject them with a hint to use the canonical form.
KNOWN_IMPOSTERS: Dict[str, str] = {
    "github:anthropic":        "github:anthropics",
    "github:openai-official":  "github:openai",
    "github:0penai":           "github:openai",
    "github:huggingface-ai":   "github:huggingface",
    "github:stoa-ai":          "github:stoa-network",
    "github:stoaxyz":          "github:stoa-network",
    "did:web:anthropic.ai":    "did:web:anthropic.com",
    "did:web:0penai.com":      "did:web:openai.com",
}


def validate_author_field(
    author_value: object,
    *,
    require_binding: Optional[bool] = None,
) -> AuthorVerdict:
    """Validate the ``author:`` frontmatter value.

    ``require_binding=None`` defers to ``is_signature_required()``.
    Pass ``True``/``False`` to force the check on or off (used by tests).
    """
    if require_binding is None:
        require_binding = is_signature_required()

    if not isinstance(author_value, str) or not author_value.strip():
        if require_binding:
            return AuthorVerdict(
                ok=False,
                reason="author field is missing or non-string",
            )
        return AuthorVerdict(
            ok=True,
            reason="author field missing (legacy mode — not enforced)",
        )

    author = author_value.strip()

    # Reject known imposters before checking format — the imposter values
    # ARE valid format but block on the lookup.
    canonical = KNOWN_IMPOSTERS.get(author.lower())
    if canonical is not None:
        return AuthorVerdict(
            ok=False,
            reason=(
                f"author {author!r} matches known imposter list — "
                f"use canonical {canonical!r} or a different identity"
            ),
            canonical_identity=canonical,
        )

    gh_match = AUTHOR_GITHUB_RE.match(author)
    did_match = AUTHOR_DID_WEB_RE.match(author)

    if gh_match or did_match:
        return AuthorVerdict(
            ok=True,
            reason="author identity binding valid",
            canonical_identity=author,
        )

    if require_binding:
        return AuthorVerdict(
            ok=False,
            reason=(
                f"author {author!r} must use a binding prefix "
                "(github:<org> or did:web:<host>) under "
                "STOA_SKILL_REQUIRE_SIGNATURE=1"
            ),
        )

    return AuthorVerdict(
        ok=True,
        reason="author field unprefixed (legacy mode — not enforced)",
        canonical_identity=author,
    )


# ---------------------------------------------------------------------------
# Push-update diff classification
# ---------------------------------------------------------------------------

def classify_update_diff(
    previous_files: Iterable[str],
    new_files: Iterable[str],
    *,
    metadata_only_extensions: Iterable[str] = (".md", ".txt"),
) -> UpdateDiffVerdict:
    """Classify an upstream update as ``silent`` / ``warn`` / ``block``.

    Audit v11 HIGH-61 finding #4: a published skill that previously had
    three files can have ``scripts/exfil.sh`` added on the next upstream
    push without any user re-prompt because the only thing we tracked
    was the content hash.

    Decision tree:

      * Any NEW file outside ``metadata_only_extensions``  → ``warn``
        (triggers re-confirm in the caller).
      * Any executable extension added (.py/.sh/.js/.ts/...)  → ``warn``.
      * Only metadata files (.md, .txt) changed/added       → ``silent``
        (auto-apply on update).
      * No diff at all                                       → ``silent``.

    The ``block`` verdict is reserved for future expansion (e.g. a file
    is removed from the bundle but the LOCK file references it) and is
    not currently returned.
    """
    prev_set = {str(p) for p in previous_files}
    new_set = {str(p) for p in new_files}

    added = sorted(new_set - prev_set)
    removed = sorted(prev_set - new_set)
    # ``modified`` is best-effort here — the caller has the hashes and
    # can refine this; we just report file-presence diffs.
    common = sorted(prev_set & new_set)

    safe_exts = {ext.lower() for ext in metadata_only_extensions}

    def _is_metadata(path: str) -> bool:
        lowered = path.lower()
        return any(lowered.endswith(ext) for ext in safe_exts)

    risky_added = [p for p in added if not _is_metadata(p)]

    if not added and not removed:
        return UpdateDiffVerdict(
            classification="silent",
            reason="no file additions or removals",
            modified_files=common,
        )

    if risky_added:
        return UpdateDiffVerdict(
            classification="warn",
            reason=(
                f"{len(risky_added)} non-metadata file(s) added to bundle "
                "since last install — re-confirm required"
            ),
            added_files=added,
            removed_files=removed,
            modified_files=common,
        )

    return UpdateDiffVerdict(
        classification="silent",
        reason="only metadata files (*.md/*.txt) changed",
        added_files=added,
        removed_files=removed,
        modified_files=common,
    )


# ---------------------------------------------------------------------------
# Publish target-repo binding
# ---------------------------------------------------------------------------

_TARGET_REPO_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9\-]{0,38})/[A-Za-z0-9._\-]{1,100}$")


def check_publish_target_repo(
    declared_author: str,
    target_repo: str,
) -> TargetRepoVerdict:
    """Validate a ``stoa skill publish`` target repository.

    Audit v11 HIGH-61 finding #5: an installed skill that has access to
    the user's ``GITHUB_TOKEN`` could call out to ``stoa skill publish``
    against an arbitrary upstream repository — pushing code (or test
    bundles, or signed attestations) under the user's identity to a
    repo the user does not own.

    Mitigation: the declared ``author:`` field (which under
    ``STOA_SKILL_REQUIRE_SIGNATURE=1`` is bound to ``github:<org>``) MUST
    match the ``<org>`` segment of the target repo's ``owner/name``.
    """
    if not isinstance(target_repo, str) or not target_repo.strip():
        return TargetRepoVerdict(ok=False, reason="target_repo is empty")

    target_repo = target_repo.strip()
    m = _TARGET_REPO_RE.match(target_repo)
    if not m:
        return TargetRepoVerdict(
            ok=False,
            reason=f"target_repo {target_repo!r} is not a valid owner/name slug",
        )

    target_org = m.group(1).lower()

    if not isinstance(declared_author, str) or not declared_author.strip():
        return TargetRepoVerdict(
            ok=False,
            reason="declared author identity is empty",
            target_org=target_org,
        )

    gh = AUTHOR_GITHUB_RE.match(declared_author.strip())
    if not gh:
        return TargetRepoVerdict(
            ok=False,
            reason=(
                f"declared author {declared_author!r} is not a "
                "github:<org> binding — refusing to push to an arbitrary "
                "third-party repo"
            ),
            declared_identity=declared_author,
            target_org=target_org,
        )

    declared_org = gh.group(1).lower()
    if declared_org != target_org:
        return TargetRepoVerdict(
            ok=False,
            reason=(
                f"declared author org {declared_org!r} != target repo "
                f"org {target_org!r} — refusing cross-org publish"
            ),
            declared_identity=declared_author,
            target_org=target_org,
        )

    return TargetRepoVerdict(
        ok=True,
        reason="declared author org matches target repo org",
        declared_identity=declared_author,
        target_org=target_org,
    )


__all__ = [
    "is_signature_required",
    "SignatureVerdict",
    "RevocationVerdict",
    "AuthorVerdict",
    "UpdateDiffVerdict",
    "TargetRepoVerdict",
    "verify_bundle_signature",
    "sign_bundle",
    "is_sha_revoked",
    "revoke_sha",
    "validate_author_field",
    "classify_update_diff",
    "check_publish_target_repo",
    "ENV_REQUIRE_SIGNATURE",
    "SIGNATURE_FILENAME",
    "REVOCATIONS_FILE",
    "TRUSTED_SIGNERS_FILE",
]
