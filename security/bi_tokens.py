from __future__ import annotations

import hashlib
import hmac
import ipaddress
import secrets
from typing import Final

from security.key_manager import KeyManager, key_manager

BI_TOKEN_PREFIX: Final[str] = "ctx_bi_"


def is_bi_bearer_token(raw_token: str | None) -> bool:
    return (raw_token or "").strip().startswith(BI_TOKEN_PREFIX)


def hash_bi_token(raw_token: str, km: KeyManager | None = None) -> str:
    active_km = km or key_manager
    clean = (raw_token or "").strip()
    return hmac.new(
        active_km.pepper_key,
        clean.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def generate_bi_token(km: KeyManager | None = None) -> tuple[str, str, str]:
    raw_token = BI_TOKEN_PREFIX + secrets.token_urlsafe(36)
    token_prefix = raw_token[:14]
    token_sha256 = hash_bi_token(raw_token, km=km)
    return raw_token, token_prefix, token_sha256


def validate_cidr_list(allowed_cidr: str) -> str:
    raw = (allowed_cidr or "").strip()
    if not raw:
        return ""
    normalized: list[str] = []
    for part in raw.split(","):
        item = part.strip()
        if not item:
            continue
        try:
            net = ipaddress.ip_network(item, strict=False)
        except ValueError as exc:
            raise ValueError(f"Faixa IP/CIDR inválida: {item}") from exc
        normalized.append(str(net))
    return ", ".join(normalized)


def is_ip_allowed_by_cidr(client_ip: str, allowed_cidr: str) -> bool:
    raw_cidr = (allowed_cidr or "").strip()
    if not raw_cidr:
        return True
    entries = [p.strip() for p in raw_cidr.split(",") if p.strip()]
    if not entries:
        return True
    try:
        addr = ipaddress.ip_address((client_ip or "").strip())
    except ValueError:
        return False

    for entry in entries:
        try:
            net = ipaddress.ip_network(entry, strict=False)
        except ValueError:
            return False
        if addr.version == net.version and addr in net:
            return True
    return False
