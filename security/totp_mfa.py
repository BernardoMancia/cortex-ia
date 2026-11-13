from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import struct
import threading
import time
from urllib.parse import quote

_QR_VERSION_CAP_L: dict[int, tuple[int, int, int]] = {
    1: (21, 19, 7),
    2: (25, 34, 10),
    3: (29, 55, 15),
    4: (33, 80, 20),
    5: (37, 108, 26),
    6: (41, 136, 36),
    7: (45, 156, 40),
    8: (49, 194, 48),
}

_QR_ALIGN_POS: dict[int, list[int]] = {
    1: [],
    2: [6, 18],
    3: [6, 22],
    4: [6, 26],
    5: [6, 30],
    6: [6, 34],
    7: [6, 22, 38],
    8: [6, 24, 42],
}

_QR_BLOCKS_L: dict[int, list[tuple[int, int, int]]] = {
    1: [(1, 19, 7)],
    2: [(1, 34, 10)],
    3: [(1, 55, 15)],
    4: [(1, 80, 20)],
    5: [(1, 108, 26)],
    6: [(2, 68, 18)],
    7: [(2, 78, 20)],
    8: [(2, 97, 24)],
}


def _gf_tables() -> tuple[list[int], list[int]]:
    exp = [0] * 512
    log = [0] * 256
    x = 1
    for i in range(255):
        exp[i] = x
        log[x] = i
        x <<= 1
        if x & 0x100:
            x ^= 0x11D
    for i in range(255, 512):
        exp[i] = exp[i - 255]
    return exp, log


_GF_EXP, _GF_LOG = _gf_tables()


def _gf_mul(a: int, b: int) -> int:
    if a == 0 or b == 0:
        return 0
    return _GF_EXP[_GF_LOG[a] + _GF_LOG[b]]


def _rs_generator(degree: int) -> list[int]:
    poly = [1]
    for i in range(degree):
        nxt = [0] * (len(poly) + 1)
        factor = _GF_EXP[i]
        for j, coef in enumerate(poly):
            nxt[j] ^= coef
            nxt[j + 1] ^= _gf_mul(coef, factor)
        poly = nxt
    return poly


def _rs_encode(data: list[int], ec_len: int) -> list[int]:
    gen = _rs_generator(ec_len)
    res = [0] * ec_len
    for byte in data:
        factor = byte ^ res[0]
        res = res[1:] + [0]
        for i in range(ec_len):
            res[i] ^= _gf_mul(gen[i + 1], factor)
    return res


def _bch_format_bits(ec_level_bits: int, mask: int) -> int:
    data = (ec_level_bits << 3) | mask
    rem = data << 10
    for i in range(4, -1, -1):
        if rem & (1 << (i + 10)):
            rem ^= 0x537 << i
    return ((data << 10) | rem) ^ 0x5412


def _bch_version_bits(version: int) -> int:
    rem = version << 12
    for i in range(5, -1, -1):
        if rem & (1 << (i + 12)):
            rem ^= 0x1F25 << i
    return (version << 12) | rem


def render_qr_svg_offline(text: str, module_px: int = 5, border: int = 4) -> str:
    payload = text.encode("utf-8")
    selected_v = 0
    for v in range(1, 9):
        _, total_data_cw, _ = _QR_VERSION_CAP_L[v]
        max_bytes = total_data_cw - 2
        if len(payload) <= max_bytes:
            selected_v = v
            break
    if selected_v == 0:
        raise ValueError("Payload excede capacidade QR offline v8-L.")

    size, total_data_cw, _ = _QR_VERSION_CAP_L[selected_v]
    bits: list[int] = [0, 1, 0, 0]
    for b in range(7, -1, -1):
        bits.append((len(payload) >> b) & 1)
    for byte in payload:
        for b in range(7, -1, -1):
            bits.append((byte >> b) & 1)
    max_bits = total_data_cw * 8
    bits.extend([0] * min(4, max_bits - len(bits)))
    while len(bits) % 8 != 0:
        bits.append(0)

    codewords: list[int] = []
    for i in range(0, len(bits), 8):
        val = 0
        for b in bits[i : i + 8]:
            val = (val << 1) | b
        codewords.append(val)
    pads = [0xEC, 0x11]
    pad_idx = 0
    while len(codewords) < total_data_cw:
        codewords.append(pads[pad_idx % 2])
        pad_idx += 1

    data_blocks: list[list[int]] = []
    ec_blocks: list[list[int]] = []
    cursor = 0
    for count, data_cw, ec_cw in _QR_BLOCKS_L[selected_v]:
        for _ in range(count):
            blk = codewords[cursor : cursor + data_cw]
            cursor += data_cw
            data_blocks.append(blk)
            ec_blocks.append(_rs_encode(blk, ec_cw))

    interleaved: list[int] = []
    max_d = max(len(b) for b in data_blocks)
    for col in range(max_d):
        for blk in data_blocks:
            if col < len(blk):
                interleaved.append(blk[col])
    max_e = max(len(b) for b in ec_blocks)
    for col in range(max_e):
        for blk in ec_blocks:
            if col < len(blk):
                interleaved.append(blk[col])

    stream_bits: list[int] = []
    for cw in interleaved:
        for b in range(7, -1, -1):
            stream_bits.append((cw >> b) & 1)

    grid: list[list[int | None]] = [[None] * size for _ in range(size)]
    reserved: list[list[bool]] = [[False] * size for _ in range(size)]

    def set_fn(r: int, c: int, dark: bool) -> None:
        if 0 <= r < size and 0 <= c < size:
            grid[r][c] = 1 if dark else 0
            reserved[r][c] = True

    def place_finder(top: int, left: int) -> None:
        for dr in range(-1, 8):
            for dc in range(-1, 8):
                rr, cc = top + dr, left + dc
                if 0 <= rr < size and 0 <= cc < size:
                    in_outer = 0 <= dr <= 6 and 0 <= dc <= 6 and (dr in (0, 6) or dc in (0, 6))
                    in_inner = 2 <= dr <= 4 and 2 <= dc <= 4
                    set_fn(rr, cc, in_outer or in_inner)

    place_finder(0, 0)
    place_finder(0, size - 7)
    place_finder(size - 7, 0)

    for i in range(8, size - 8):
        set_fn(6, i, i % 2 == 0)
        set_fn(i, 6, i % 2 == 0)

    centers = _QR_ALIGN_POS[selected_v]
    for r_c in centers:
        for c_c in centers:
            if (r_c == 6 and c_c == 6) or (r_c == 6 and c_c == size - 7) or (r_c == size - 7 and c_c == 6):
                continue
            for dr in range(-2, 3):
                for dc in range(-2, 3):
                    dark = max(abs(dr), abs(dc)) in (0, 2)
                    set_fn(r_c + dr, c_c + dc, dark)

    for i in range(9):
        if i != 6:
            reserved[8][i] = True
            reserved[i][8] = True
    for i in range(8):
        reserved[8][size - 1 - i] = True
        reserved[size - 1 - i][8] = True
    set_fn(size - 8, 8, True)

    if selected_v >= 7:
        vbits = _bch_version_bits(selected_v)
        for i in range(18):
            bit = bool((vbits >> i) & 1)
            r, c = i // 3, (size - 11) + (i % 3)
            set_fn(r, c, bit)
            set_fn(c, r, bit)

    bit_idx = 0
    upward = True
    col = size - 1
    while col > 0:
        if col == 6:
            col -= 1
        rows = range(size - 1, -1, -1) if upward else range(size)
        for r in rows:
            for dc in (0, -1):
                cc = col + dc
                if not reserved[r][cc]:
                    val = stream_bits[bit_idx] if bit_idx < len(stream_bits) else 0
                    bit_idx += 1
                    if (r + cc) % 2 == 0:
                        val ^= 1
                    grid[r][cc] = val
        upward = not upward
        col -= 2

    fmt = _bch_format_bits(ec_level_bits=0b01, mask=0)
    fmt_coords_a = [
        (8, 0), (8, 1), (8, 2), (8, 3), (8, 4), (8, 5), (8, 7), (8, 8),
        (7, 8), (5, 8), (4, 8), (3, 8), (2, 8), (1, 8), (0, 8),
    ]
    for i, (r, c) in enumerate(fmt_coords_a):
        grid[r][c] = (fmt >> i) & 1
    for i in range(7):
        grid[size - 1 - i][8] = (fmt >> i) & 1
    for i in range(7, 15):
        grid[8][size - 15 + i] = (fmt >> i) & 1

    full_dim = (size + border * 2) * module_px
    rects: list[str] = []
    for r in range(size):
        for c in range(size):
            if grid[r][c] == 1:
                x = (c + border) * module_px
                y = (r + border) * module_px
                rects.append(f'<rect x="{x}" y="{y}" width="{module_px}" height="{module_px}"/>')
    body = "".join(rects)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {full_dim} {full_dim}" '
        f'width="{full_dim}" height="{full_dim}" shape-rendering="crispEdges">'
        f'<rect width="100%" height="100%" fill="#ffffff"/>'
        f'<g fill="#0f172a">{body}</g></svg>'
    )


class TOTPManager:
    def __init__(self, digits: int = 6, interval: int = 30) -> None:
        self.digits = digits
        self.interval = interval
        self._lock = threading.Lock()
        self._used_slots: dict[tuple[int, int], float] = {}

    @staticmethod
    def generate_secret() -> str:
        return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")

    @staticmethod
    def build_provisioning_uri(secret_b32: str, username: str, issuer: str = "CortexIA") -> str:
        label = quote(f"{issuer}:{username}")
        return f"otpauth://totp/{label}?secret={secret_b32}&issuer={quote(issuer)}&algorithm=SHA1&digits=6&period=30"

    def _hotp(self, secret_b32: str, counter: int) -> str:
        padded = secret_b32.strip().upper().replace(" ", "")
        rem = len(padded) % 8
        if rem:
            padded += "=" * (8 - rem)
        key = base64.b32decode(padded, casefold=True)
        msg = struct.pack(">Q", int(counter))
        digest = hmac.new(key, msg, hashlib.sha1).digest()
        offset = digest[-1] & 0x0F
        code_int = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
        return str(code_int % (10 ** self.digits)).zfill(self.digits)

    def current_code(self, secret_b32: str, at_time: float | None = None) -> str:
        ts = time.time() if at_time is None else float(at_time)
        return self._hotp(secret_b32, int(ts // self.interval))

    def verify_code(
        self,
        user_id: int,
        secret_b32: str,
        code: str,
        at_time: float | None = None,
        allow_drift_steps: int = 1,
    ) -> bool:
        clean = (code or "").strip().replace(" ", "")
        if len(clean) != self.digits or not clean.isdigit():
            return False
        now = time.time() if at_time is None else float(at_time)
        base_slot = int(now // self.interval)

        with self._lock:
            stale = [k for k, exp in self._used_slots.items() if exp < now]
            for k in stale:
                self._used_slots.pop(k, None)

            for delta in range(-allow_drift_steps, allow_drift_steps + 1):
                slot = base_slot + delta
                if (int(user_id), slot) in self._used_slots:
                    continue
                expected = self._hotp(secret_b32, slot)
                if hmac.compare_digest(clean, expected):
                    self._used_slots[(int(user_id), slot)] = now + (self.interval * 3)
                    return True
        return False

    @staticmethod
    def generate_recovery_codes(count: int = 8) -> tuple[list[str], str]:
        plain_codes: list[str] = []
        hashed_entries: list[dict[str, str]] = []
        for _ in range(count):
            raw = f"{secrets.token_hex(2).upper()}-{secrets.token_hex(2).upper()}"
            salt = secrets.token_hex(8)
            digest = hashlib.sha256(f"{salt}:{raw}".encode("utf-8")).hexdigest()
            plain_codes.append(raw)
            hashed_entries.append({"salt": salt, "hash": digest})
        return plain_codes, json.dumps(hashed_entries)

    @staticmethod
    def consume_recovery_code(candidate: str, stored_json: str) -> tuple[bool, str]:
        clean = (candidate or "").strip().upper()
        if not clean or not stored_json:
            return False, stored_json
        try:
            entries = json.loads(stored_json)
            if not isinstance(entries, list):
                return False, stored_json
        except Exception:
            return False, stored_json

        remaining: list[dict[str, str]] = []
        matched = False
        for item in entries:
            salt = str(item.get("salt", ""))
            expected_h = str(item.get("hash", ""))
            calc = hashlib.sha256(f"{salt}:{clean}".encode("utf-8")).hexdigest()
            if not matched and hmac.compare_digest(calc, expected_h):
                matched = True
            else:
                remaining.append(item)
        return matched, json.dumps(remaining)


totp_manager = TOTPManager()
