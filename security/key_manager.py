from __future__ import annotations

import base64
import hashlib
import os
from pathlib import Path
import secrets
import sys
import threading

from config.settings import settings


class KeyManager:
    def __init__(self, keys_dir: Path | None = None) -> None:
        self._keys_dir = Path(keys_dir) if keys_dir is not None else settings.KEYS_DIR
        self._lock = threading.Lock()
        self._cache: dict[str, bytes] = {}

    def _load_or_create_key(self, name: str, env_var: str) -> bytes:
        with self._lock:
            if name in self._cache:
                return self._cache[name]

            env_val = os.getenv(env_var, "").strip()
            if len(env_val) >= 32:
                try:
                    raw = bytes.fromhex(env_val)
                    if len(raw) >= 32:
                        self._cache[name] = raw[:32]
                        return self._cache[name]
                except ValueError:
                    pass
                derived = hashlib.sha256(env_val.encode("utf-8")).digest()
                self._cache[name] = derived
                return derived

            self._keys_dir.mkdir(parents=True, exist_ok=True)
            if sys.platform != "win32":
                try:
                    os.chmod(self._keys_dir, 0o700)
                except OSError:
                    pass

            key_file = self._keys_dir / f"{name}.key"
            if key_file.exists():
                encoded = key_file.read_text(encoding="utf-8").strip()
                raw_bytes = base64.urlsafe_b64decode(encoded.encode("ascii"))
                if len(raw_bytes) >= 32:
                    self._cache[name] = raw_bytes[:32]
                    return self._cache[name]

            fresh = secrets.token_bytes(32)
            tmp_file = self._keys_dir / f"{name}.key.tmp"
            tmp_file.write_text(base64.urlsafe_b64encode(fresh).decode("ascii"), encoding="utf-8")
            if sys.platform != "win32":
                try:
                    os.chmod(tmp_file, 0o600)
                except OSError:
                    pass
            tmp_file.replace(key_file)
            if sys.platform != "win32":
                try:
                    os.chmod(key_file, 0o600)
                except OSError:
                    pass
            self._cache[name] = fresh
            return fresh

    @property
    def master_key(self) -> bytes:
        return self._load_or_create_key("master", "CORTEX_MASTER_KEY")

    @property
    def pepper_key(self) -> bytes:
        return self._load_or_create_key("pepper", "CORTEX_PEPPER_KEY")

    @property
    def csrf_key(self) -> bytes:
        return self._load_or_create_key("csrf", "CORTEX_CSRF_SECRET")

    @property
    def audit_key(self) -> bytes:
        return self._load_or_create_key("audit", "CORTEX_AUDIT_KEY")


key_manager = KeyManager()
