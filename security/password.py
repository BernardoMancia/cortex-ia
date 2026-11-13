from __future__ import annotations

import hashlib
import hmac
import secrets
import string

from security.key_manager import KeyManager, key_manager

PBKDF2_ITERATIONS: int = 600_000

_WEAK_PASSWORDS = frozenset({
    "admin",
    "admin123",
    "12345678",
    "123456789",
    "1234567890",
    "password",
    "password123",
    "qwerty123",
    "cortex123",
    "clear123",
    "senha123",
    "mudar123",
})

_DUMMY_SALT = secrets.token_bytes(32)
_DUMMY_DIGEST = hashlib.pbkdf2_hmac(
    "sha256",
    b"cortex_timing_mitigation_guard_constant",
    _DUMMY_SALT,
    PBKDF2_ITERATIONS,
)


class PasswordSecurity:
    def __init__(self, km: KeyManager | None = None) -> None:
        self._km = km or key_manager

    def _peppered_input(self, password: str) -> bytes:
        return hmac.new(
            self._km.pepper_key,
            (password or "").encode("utf-8"),
            hashlib.sha256,
        ).digest()

    def hash_password(self, password: str) -> tuple[str, str]:
        salt = secrets.token_bytes(32)
        dk = hashlib.pbkdf2_hmac(
            "sha256",
            self._peppered_input(password),
            salt,
            PBKDF2_ITERATIONS,
        )
        return f"pbkdf2_peppered_sha256${PBKDF2_ITERATIONS}${dk.hex()}", salt.hex()

    def verify_password(self, password: str, stored_hash: str, salt_hex: str) -> bool:
        try:
            if not stored_hash or not salt_hex:
                return False
            salt = bytes.fromhex(salt_hex)
            if stored_hash.startswith("pbkdf2_peppered_sha256$"):
                _, iters_s, digest_hex = stored_hash.split("$", 2)
                expected = bytes.fromhex(digest_hex)
                computed = hashlib.pbkdf2_hmac(
                    "sha256",
                    self._peppered_input(password),
                    salt,
                    int(iters_s),
                )
                return hmac.compare_digest(computed, expected)
            return False
        except Exception:
            return False

    def dummy_verify(self, password: str = "") -> None:
        peppered = self._peppered_input(password)
        computed = hashlib.pbkdf2_hmac("sha256", peppered, _DUMMY_SALT, PBKDF2_ITERATIONS)
        hmac.compare_digest(computed, _DUMMY_DIGEST)

    @staticmethod
    def validate_password_policy(password: str, username: str = "") -> tuple[bool, str]:
        pwd = password or ""
        if len(pwd) < 8:
            return False, "A senha deve ter no mínimo 8 caracteres."
        if len(pwd) > 128:
            return False, "A senha não pode exceder 128 caracteres."
        lower = pwd.strip().lower()
        if lower in _WEAK_PASSWORDS:
            return False, "Esta senha é comum/padrão e foi bloqueada pela política de segurança."
        if username and len(username.strip()) >= 3 and username.strip().lower() in lower:
            return False, "A senha não pode conter o seu nome de usuário."
        categories = sum([
            any(c.islower() for c in pwd),
            any(c.isupper() for c in pwd),
            any(c.isdigit() for c in pwd),
            any(not c.isalnum() for c in pwd),
        ])
        if categories < 2:
            return False, "A senha deve combinar letras e números ou símbolos."
        return True, "OK"

    @staticmethod
    def generate_temporary_password(length: int = 14) -> str:
        safe_len = max(12, min(int(length), 32))
        alphabet = string.ascii_letters + string.digits + "@!#%&*?"
        while True:
            candidate = "".join(secrets.choice(alphabet) for _ in range(safe_len))
            if (
                any(c.islower() for c in candidate)
                and any(c.isupper() for c in candidate)
                and any(c.isdigit() for c in candidate)
                and any(c in "@!#%&*?" for c in candidate)
            ):
                return candidate


password_security = PasswordSecurity()
