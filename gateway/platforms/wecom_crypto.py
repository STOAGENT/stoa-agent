"""WeCom BizMsgCrypt-compatible AES-CBC encryption for callback mode.

Implements the same wire format as Tencent's official ``WXBizMsgCrypt``
SDK so that WeCom can verify, encrypt, and decrypt callback payloads.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import socket
import struct
import time
from typing import Optional
from xml.etree import ElementTree as ET

from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


class WeComCryptoError(Exception):
    pass


class SignatureError(WeComCryptoError):
    pass


class DecryptError(WeComCryptoError):
    pass


class EncryptError(WeComCryptoError):
    pass


class PKCS7Encoder:
    block_size = 32

    @classmethod
    def encode(cls, text: bytes) -> bytes:
        amount_to_pad = cls.block_size - (len(text) % cls.block_size)
        if amount_to_pad == 0:
            amount_to_pad = cls.block_size
        pad = bytes([amount_to_pad]) * amount_to_pad
        return text + pad

    @classmethod
    def decode(cls, decrypted: bytes) -> bytes:
        if not decrypted:
            raise DecryptError("empty decrypted payload")
        pad = decrypted[-1]
        if pad < 1 or pad > cls.block_size:
            raise DecryptError("invalid PKCS7 padding")
        if decrypted[-pad:] != bytes([pad]) * pad:
            raise DecryptError("malformed PKCS7 padding")
        return decrypted[:-pad]


def _sha1_signature(token: str, timestamp: str, nonce: str, encrypt: str) -> str:
    parts = sorted([token, timestamp, nonce, encrypt])
    return hashlib.sha1("".join(parts).encode("utf-8")).hexdigest()


class WXBizMsgCrypt:
    """Minimal WeCom callback crypto helper compatible with BizMsgCrypt semantics."""

    def __init__(self, token: str, encoding_aes_key: str, receive_id: str):
        if not token:
            raise ValueError("token is required")
        if not encoding_aes_key:
            raise ValueError("encoding_aes_key is required")
        if len(encoding_aes_key) != 43:
            raise ValueError("encoding_aes_key must be 43 chars")
        if not receive_id:
            raise ValueError("receive_id is required")

        self.token = token
        self.receive_id = receive_id
        self.key = base64.b64decode(encoding_aes_key + "=")
        # WeCom protocol mandates that the AES-CBC IV equal the first 16
        # bytes of the AES key (see WeCom open-platform crypto spec). We
        # cannot deviate from this without breaking interop with WeCom's
        # own servers — every message exchanged with WeCom would fail
        # decryption on either side. Audit Phase-1A documents this as a
        # protocol-enforced weakness (mitigated upstream by the 16-byte
        # random `random_prefix` prepended to every plaintext, see
        # `_encrypt_bytes`, which gives effective per-message IV-like
        # entropy even though the AES IV itself is deterministic).
        # The IV is computed via a private helper rather than an
        # inline slice so callers don't think they can change it.
        self.iv = self._protocol_iv(self.key)

    @staticmethod
    def _protocol_iv(key: bytes) -> bytes:
        # See above. Equivalent to ``key[:16]`` but isolated so the
        # protocol-mandated nature of this choice is documented in
        # exactly one place.
        return bytes(key[0:16])

    def verify_url(self, msg_signature: str, timestamp: str, nonce: str, echostr: str) -> str:
        plain = self.decrypt(msg_signature, timestamp, nonce, echostr)
        return plain.decode("utf-8")

    def decrypt(self, msg_signature: str, timestamp: str, nonce: str, encrypt: str) -> bytes:
        # Audit v4 HIGH G-05 + v7 HIGH-10 fix: timestamp freshness check.
        # Without this, any signed POST captured from the wire (LAN sniffer,
        # mitm proxy, screen recording) could be replayed indefinitely after
        # the per-id dedup window expires (5 min default). 10-minute window
        # is the WeCom server's own clock-skew tolerance — anything older
        # is the same hash as something we'd already have seen + dedup'd.
        try:
            ts = int(timestamp)
        except (TypeError, ValueError):
            raise SignatureError("timestamp not an integer")
        now = int(time.time())
        if abs(now - ts) > 600:  # 10 min
            raise SignatureError(f"timestamp out of freshness window: ts={ts} now={now}")

        expected = _sha1_signature(self.token, timestamp, nonce, encrypt)
        # Audit v4 G-04 fix: constant-time compare. The previous `!=` leaks
        # the first-byte-mismatch position via a timing oracle. SHA-1 hex
        # has only 40 chars but compare_digest is still the right answer.
        if not hmac.compare_digest(expected, msg_signature):
            raise SignatureError("signature mismatch")
        try:
            cipher_text = base64.b64decode(encrypt)
        except Exception as exc:
            raise DecryptError(f"invalid base64 payload: {exc}") from exc
        try:
            cipher = Cipher(algorithms.AES(self.key), modes.CBC(self.iv), backend=default_backend())
            decryptor = cipher.decryptor()
            padded = decryptor.update(cipher_text) + decryptor.finalize()
            plain = PKCS7Encoder.decode(padded)
            content = plain[16:]  # skip 16-byte random prefix
            xml_length = socket.ntohl(struct.unpack("I", content[:4])[0])
            xml_content = content[4:4 + xml_length]
            receive_id = content[4 + xml_length:].decode("utf-8")
        except WeComCryptoError:
            raise
        except Exception as exc:
            raise DecryptError(f"decrypt failed: {exc}") from exc

        if receive_id != self.receive_id:
            raise DecryptError("receive_id mismatch")
        return xml_content

    def encrypt(self, plaintext: str, nonce: Optional[str] = None, timestamp: Optional[str] = None) -> str:
        nonce = nonce or self._random_nonce()
        timestamp = timestamp or str(int(__import__("time").time()))
        encrypt = self._encrypt_bytes(plaintext.encode("utf-8"))
        signature = _sha1_signature(self.token, timestamp, nonce, encrypt)
        root = ET.Element("xml")
        ET.SubElement(root, "Encrypt").text = encrypt
        ET.SubElement(root, "MsgSignature").text = signature
        ET.SubElement(root, "TimeStamp").text = timestamp
        ET.SubElement(root, "Nonce").text = nonce
        return ET.tostring(root, encoding="unicode")

    def _encrypt_bytes(self, raw: bytes) -> str:
        try:
            random_prefix = os.urandom(16)
            msg_len = struct.pack("I", socket.htonl(len(raw)))
            payload = random_prefix + msg_len + raw + self.receive_id.encode("utf-8")
            padded = PKCS7Encoder.encode(payload)
            cipher = Cipher(algorithms.AES(self.key), modes.CBC(self.iv), backend=default_backend())
            encryptor = cipher.encryptor()
            encrypted = encryptor.update(padded) + encryptor.finalize()
            return base64.b64encode(encrypted).decode("utf-8")
        except Exception as exc:
            raise EncryptError(f"encrypt failed: {exc}") from exc

    @staticmethod
    def _random_nonce(length: int = 10) -> str:
        alphabet = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
        return "".join(secrets.choice(alphabet) for _ in range(length))
