from __future__ import annotations

import ipaddress
import re
import socket
from urllib.parse import urlparse
from xml.etree import ElementTree

import requests

_FORBIDDEN_XML_DECLARATIONS = re.compile(r"<!\s*(?:DOCTYPE|ENTITY)\b", re.IGNORECASE)
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]")


def is_public_ip_address(ip_str: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    if (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_reserved
        or addr.is_unspecified
    ):
        return False
    return True


def validate_external_https_url(url: str) -> str:
    clean = (url or "").strip()
    parsed = urlparse(clean)
    if parsed.scheme.lower() != "https":
        raise ValueError(f"Esquema de URL externa bloqueado (exige HTTPS): {parsed.scheme!r}")
    hostname = (parsed.hostname or "").strip()
    if not hostname:
        raise ValueError("Hostname ausente na URL externa.")
    if hostname.lower() in ("localhost", "ip6-localhost", "metadata.google.internal"):
        raise ValueError("Hostname local/interno bloqueado por proteção Anti-SSRF.")

    try:
        literal_ip = ipaddress.ip_address(hostname)
        if not is_public_ip_address(str(literal_ip)):
            raise ValueError(f"IP não público bloqueado por Anti-SSRF: {literal_ip}")
        return clean
    except ValueError as exc:
        if "Anti-SSRF" in str(exc):
            raise

    infos = socket.getaddrinfo(hostname, parsed.port or 443, proto=socket.IPPROTO_TCP)
    if not infos:
        raise ValueError(f"Não foi possível resolver DNS para {hostname}.")
    for _, _, _, _, sockaddr in infos:
        ip_candidate = str(sockaddr[0])
        if not is_public_ip_address(ip_candidate):
            raise ValueError(f"Resolução DNS privada/loopback bloqueada (Anti-SSRF): {ip_candidate}")
    return clean


def fetch_safe_external_bytes(
    session: requests.Session,
    url: str,
    timeout: int = 12,
    max_bytes: int = 2 * 1024 * 1024,
    headers: dict[str, str] | None = None,
) -> bytes:
    validated_url = validate_external_https_url(url)
    with session.get(
        validated_url,
        timeout=timeout,
        stream=True,
        allow_redirects=False,
        headers=headers,
    ) as resp:
        if 300 <= resp.status_code < 400:
            loc = resp.headers.get("Location", "")
            if loc:
                validate_external_https_url(loc)
                with session.get(
                    loc,
                    timeout=timeout,
                    stream=True,
                    allow_redirects=False,
                    headers=headers,
                ) as redir_resp:
                    redir_resp.raise_for_status()
                    return _read_bounded_stream(redir_resp, max_bytes=max_bytes)
        resp.raise_for_status()
        return _read_bounded_stream(resp, max_bytes=max_bytes)


def _read_bounded_stream(resp: requests.Response, max_bytes: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    for chunk in resp.iter_content(chunk_size=16384):
        if not chunk:
            continue
        total += len(chunk)
        if total > max_bytes:
            raise ValueError(f"Resposta externa excedeu limite seguro de {max_bytes} bytes.")
        chunks.append(chunk)
    return b"".join(chunks)


def parse_xml_safely(xml_bytes: bytes) -> ElementTree.Element:
    sample = xml_bytes.decode("utf-8", errors="replace")
    if _FORBIDDEN_XML_DECLARATIONS.search(sample):
        raise ValueError("Declaração XML DTD/ENTITY bloqueada pela blindagem Anti-XXE.")
    return ElementTree.fromstring(sample)


def sanitize_untrusted_text(text: str, max_length: int = 280) -> str:
    cleaned = _CONTROL_CHARS_RE.sub(" ", text or "")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    cleaned = cleaned.replace("<", "‹").replace(">", "›")
    return cleaned[:max_length]
