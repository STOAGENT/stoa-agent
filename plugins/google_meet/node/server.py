"""Remote node server.

Runs on the machine that will host the Meet bot (typically the user's
Mac laptop with a signed-in Chrome). Exposes a WebSocket endpoint that
accepts signed RPC requests and dispatches them to the existing
``plugins.google_meet.process_manager`` module.

Launched by ``stoa meet node run``.

Token handling
--------------
On first boot we mint 32 hex chars of entropy and persist them at
``$STOA_HOME/workspace/meetings/node_token.json``. Subsequent boots
reuse the same token so previously-approved gateways don't need to be
re-paired. The operator copies this token out-of-band to the gateway
via ``stoa meet node approve <name> <url> <token>``.

Dependencies
------------
``websockets`` is an optional dep. We import it lazily inside
:meth:`serve` so installing the plugin doesn't require it unless you
actually host a node.
"""

from __future__ import annotations

import json
import secrets
import time
from pathlib import Path
from typing import Any, Dict, Optional

from stoa_constants import get_stoa_home
from plugins.google_meet.node import protocol as _proto


def _default_token_path() -> Path:
    return Path(get_stoa_home()) / "workspace" / "meetings" / "node_token.json"


class NodeServer:
    """WebSocket server that executes meet bot RPCs locally."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 18789,
        token_path: Optional[Path] = None,
        display_name: str = "stoa-meet-node",
    ) -> None:
        self.host = host
        self.port = port
        self.display_name = display_name
        self.token_path = Path(token_path) if token_path is not None else _default_token_path()
        self._token: Optional[str] = None

    # ----- token management --------------------------------------------

    def ensure_token(self) -> str:
        """Return the persisted shared secret, generating one on first use."""
        if self._token:
            return self._token
        if self.token_path.is_file():
            try:
                data = json.loads(self.token_path.read_text(encoding="utf-8"))
                tok = data.get("token")
                if isinstance(tok, str) and tok:
                    self._token = tok
                    return tok
            except (OSError, json.JSONDecodeError):
                pass
        tok = secrets.token_hex(16)  # 32 hex chars
        self.token_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.token_path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps({"token": tok, "generated_at": time.time()}, indent=2),
            encoding="utf-8",
        )
        # Restrict to owner-read-write only — the token grants full RPC
        # access to the meet bot (start, transcribe, speak in meetings).
        try:
            tmp.chmod(0o600)
        except (OSError, NotImplementedError):
            # Best-effort on non-POSIX filesystems; mode is set on POSIX.
            pass
        tmp.replace(self.token_path)
        self._token = tok
        return tok

    def get_token(self) -> str:
        """Alias for :meth:`ensure_token`; does not mutate on subsequent calls."""
        return self.ensure_token()

    # ----- dispatch -----------------------------------------------------

    async def _handle_request(self, msg: Dict[str, Any]) -> Dict[str, Any]:
        """Validate + dispatch a single decoded request envelope.

        Always returns a response envelope (success or error); never
        raises. Errors from inside the process_manager are wrapped into
        the response payload's ``ok``/``error`` keys (which pm already
        does) rather than being re-encoded as error envelopes — the
        envelope-level error channel is reserved for auth / protocol
        failures.
        """
        expected = self.ensure_token()
        ok, reason = _proto.validate_request(msg, expected)
        if not ok:
            return _proto.make_error(str(msg.get("id") or ""), reason)

        req_id = msg["id"]
        t = msg["type"]
        payload = msg["payload"]

        # Import lazily so test mocks can monkeypatch freely.
        from plugins.google_meet import process_manager as pm

        try:
            if t == "ping":
                return {"type": "pong", "id": req_id,
                        "payload": {"display_name": self.display_name,
                                    "ts": time.time()}}
            if t == "start_bot":
                # Whitelist kwargs we pass through to pm.start.
                kwargs = {
                    k: payload[k]
                    for k in ("url", "guest_name", "duration", "headed",
                              "auth_state", "session_id", "out_dir")
                    if k in payload
                }
                if "url" not in kwargs:
                    return _proto.make_error(req_id, "missing 'url' in payload")
                result = pm.start(**kwargs)
                return _proto.make_response(req_id, result)
            if t == "stop":
                reason_arg = payload.get("reason", "requested")
                result = pm.stop(reason=reason_arg)
                return _proto.make_response(req_id, result)
            if t == "status":
                return _proto.make_response(req_id, pm.status())
            if t == "transcript":
                last = payload.get("last")
                result = pm.transcript(last=last)
                return _proto.make_response(req_id, result)
            if t == "say":
                # v2 wiring: enqueue into say_queue.jsonl inside the
                # active meeting's out_dir when present. The bot-side
                # consumer is v3+ (for v1 this is a stub returning ok).
                text = payload.get("text", "")
                active = pm._read_active()  # type: ignore[attr-defined]
                enqueued = False
                if active and active.get("out_dir"):
                    queue = Path(active["out_dir"]) / "say_queue.jsonl"
                    try:
                        queue.parent.mkdir(parents=True, exist_ok=True)
                        with queue.open("a", encoding="utf-8") as fh:
                            fh.write(json.dumps({"text": text, "ts": time.time()}) + "\n")
                        enqueued = True
                    except OSError:
                        enqueued = False
                return _proto.make_response(
                    req_id,
                    {"ok": True, "enqueued": enqueued, "text": text},
                )
        except Exception as exc:  # noqa: BLE001 — surface any pm crash to client
            return _proto.make_error(req_id, f"{type(exc).__name__}: {exc}")

        return _proto.make_error(req_id, f"unhandled type: {t!r}")

    # ----- server loop --------------------------------------------------

    async def serve(self) -> None:
        """Run the WebSocket server until cancelled.

        Blocks forever. Callers typically wrap this in ``asyncio.run``.
        """
        try:
            import websockets  # type: ignore
        except ImportError as exc:
            raise RuntimeError(
                "NodeServer.serve requires the 'websockets' package. "
                "Install it with: pip install websockets"
            ) from exc

        self.ensure_token()

        async def _handler(ws):
            async for raw in ws:
                try:
                    msg = _proto.decode(raw if isinstance(raw, str) else raw.decode("utf-8"))
                except ValueError as exc:
                    await ws.send(_proto.encode(_proto.make_error("", f"decode: {exc}")))
                    continue
                reply = await self._handle_request(msg)
                await ws.send(_proto.encode(reply))

        # For loopback binds (the default), no SSL is required. For non-loopback
        # (explicit --host override with STOA_MEET_NODE_ALLOW_REMOTE_BIND), demand TLS.
        _safe_loopback = {"127.0.0.1", "localhost", "::1"}
        ssl_context = None
        if self.host not in _safe_loopback:
            import os
            if os.environ.get("STOA_MEET_NODE_ALLOW_REMOTE_BIND", "").lower() not in ("1", "true", "yes"):
                raise RuntimeError(
                    f"Refusing to bind NodeServer to non-loopback host {self.host!r}: "
                    "set STOA_MEET_NODE_ALLOW_REMOTE_BIND=1 to opt in (and configure TLS)."
                )
            # JS-MEET-01: a non-loopback bind sends the 32-hex bearer token over
            # the wire, so it MUST run over TLS. Load a real cert from
            # STOA_MEET_NODE_TLS_CERT / STOA_MEET_NODE_TLS_KEY. Previously this
            # block built an SSLContext and then reset it to None ("full
            # implementation" TODO), silently downgrading to plaintext ws://
            # and leaking the token on-path. Fail closed instead.
            cert_path = os.environ.get("STOA_MEET_NODE_TLS_CERT", "")
            key_path = os.environ.get("STOA_MEET_NODE_TLS_KEY", "")
            if not cert_path or not key_path:
                raise RuntimeError(
                    "Non-loopback NodeServer bind requires TLS but no certificate is "
                    "configured. Set STOA_MEET_NODE_TLS_CERT and STOA_MEET_NODE_TLS_KEY "
                    "to PEM paths (the RPC bearer token would otherwise be sent in "
                    "cleartext over ws://)."
                )
            import ssl
            ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ssl_context.load_cert_chain(certfile=cert_path, keyfile=key_path)
        async with websockets.serve(_handler, self.host, self.port, ssl=ssl_context):
            # Run until cancelled.
            import asyncio
            await asyncio.Future()
