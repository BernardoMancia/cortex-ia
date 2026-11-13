from __future__ import annotations

import threading
from typing import Any

from brokers.base import BrokerConnector
from brokers.registry import build_wallet_connector
from storage.wallets_repo import WalletsRepository

_locks_guard = threading.Lock()
_wallet_locks: dict[int, threading.RLock] = {}


def get_wallet_lock(wallet_id: int) -> threading.RLock:
    wid = int(wallet_id)
    with _locks_guard:
        lock = _wallet_locks.get(wid)
        if lock is None:
            lock = threading.RLock()
            _wallet_locks[wid] = lock
        return lock


def resolve_connector_for_wallet(
    wallet_row: dict[str, Any],
    wallets_repo: WalletsRepository | None = None,
) -> BrokerConnector:
    repo = wallets_repo or WalletsRepository()
    user_id = int(wallet_row.get("user_id") or 0)
    wallet_id = int(wallet_row.get("id") or 0)
    decrypted: dict[str, Any] = {}
    if user_id > 0 and wallet_id > 0:
        decrypted = repo.decrypt_connector_config(user_id, wallet_id)
    return build_wallet_connector(wallet_row, decrypted_config=decrypted)
