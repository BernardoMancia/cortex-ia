from __future__ import annotations

import base64
import json
import os
from typing import Any

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from security.key_manager import KeyManager, key_manager


class WalletCryptoVault:
    def __init__(self, km: KeyManager | None = None) -> None:
        self._km = km or key_manager

    def _derive_key_and_aad(
        self,
        purpose: str,
        user_id: int,
        entity_id: int | str,
    ) -> tuple[bytes, bytes]:
        context_bytes = f"cortex-v1:{purpose}:u={int(user_id)}:e={entity_id}".encode("utf-8")
        hkdf = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=None,
            info=context_bytes,
        )
        subkey = hkdf.derive(self._km.master_key)
        return subkey, context_bytes

    def encrypt_payload(
        self,
        data: dict[str, Any] | str,
        purpose: str,
        user_id: int,
        entity_id: int | str,
    ) -> str:
        subkey, aad = self._derive_key_and_aad(purpose, user_id, entity_id)
        nonce = os.urandom(12)
        if isinstance(data, str):
            plaintext = data.encode("utf-8")
        else:
            plaintext = json.dumps(data, separators=(",", ":"), sort_keys=True).encode("utf-8")
        ciphertext = AESGCM(subkey).encrypt(nonce, plaintext, aad)
        envelope = nonce + ciphertext
        return "v1:" + base64.urlsafe_b64encode(envelope).decode("ascii")

    def decrypt_payload(
        self,
        token: str,
        purpose: str,
        user_id: int,
        entity_id: int | str,
    ) -> bytes:
        if not token or not token.startswith("v1:"):
            raise ValueError("Envelope criptográfico inválido ou ausente.")
        raw = base64.urlsafe_b64decode(token[3:].encode("ascii"))
        if len(raw) < 28:
            raise ValueError("Envelope criptográfico truncado.")
        nonce, ciphertext = raw[:12], raw[12:]
        subkey, aad = self._derive_key_and_aad(purpose, user_id, entity_id)
        return AESGCM(subkey).decrypt(nonce, ciphertext, aad)

    def encrypt_wallet_config(
        self,
        user_id: int,
        wallet_id: int,
        config: dict[str, Any],
    ) -> str:
        return self.encrypt_payload(config, purpose="wallet_cfg", user_id=user_id, entity_id=wallet_id)

    def decrypt_wallet_config(
        self,
        user_id: int,
        wallet_id: int,
        encrypted_blob: str,
    ) -> dict[str, Any]:
        if not encrypted_blob:
            return {}
        plain = self.decrypt_payload(encrypted_blob, purpose="wallet_cfg", user_id=user_id, entity_id=wallet_id)
        parsed = json.loads(plain.decode("utf-8"))
        return parsed if isinstance(parsed, dict) else {}

    def encrypt_totp_secret(self, user_id: int, secret_b32: str) -> str:
        return self.encrypt_payload(secret_b32, purpose="totp_secret", user_id=user_id, entity_id=user_id)

    def decrypt_totp_secret(self, user_id: int, encrypted_secret: str) -> str:
        plain = self.decrypt_payload(encrypted_secret, purpose="totp_secret", user_id=user_id, entity_id=user_id)
        return plain.decode("utf-8")


wallet_crypto = WalletCryptoVault()
