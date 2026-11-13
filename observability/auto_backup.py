from __future__ import annotations

import os
from pathlib import Path
import sqlite3
import sys
import tempfile

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from config.settings import settings
from domain.b3_rules import get_brt_now
from observability.logger import get_logger
from security.key_manager import key_manager

logger = get_logger("auto_backup")


class EncryptedBackupService:
    def __init__(self, db_path: Path | None = None, backups_dir: Path | None = None) -> None:
        self.db_path = Path(db_path) if db_path is not None else settings.DB_PATH
        self.backups_dir = Path(backups_dir) if backups_dir is not None else settings.BACKUPS_DIR

    def create_verified_encrypted_snapshot(self, tag: str = "scheduled", keep_last: int = 14) -> Path | None:
        if not self.db_path.exists():
            return None
        self.backups_dir.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        tmp_path = Path(tmp_name)
        try:
            src = sqlite3.connect(str(self.db_path), timeout=15.0)
            dst = sqlite3.connect(str(tmp_path), timeout=15.0)
            try:
                src.backup(dst)
                row = dst.execute("PRAGMA integrity_check;").fetchone()
                if not row or str(row[0]).lower() != "ok":
                    raise RuntimeError(f"integrity_check falhou: {row}")
            finally:
                dst.close()
                src.close()

            raw_bytes = tmp_path.read_bytes()
            nonce = os.urandom(12)
            aad = f"cortex-db-backup:{settings.ENVIRONMENT_ID}:v{settings.VERSION}".encode("utf-8")
            ciphertext = AESGCM(key_manager.master_key).encrypt(nonce, raw_bytes, aad)

            stamp = get_brt_now().strftime("%Y-%m-%d_%H%M%S")
            out_file = self.backups_dir / f"auto_db_v{settings.VERSION}_{tag}_{stamp}.db.enc"
            out_file.write_bytes(b"CXBK1" + nonce + ciphertext)
            if sys.platform != "win32":
                try:
                    os.chmod(out_file, 0o600)
                except OSError:
                    pass
            self._prune_old_snapshots(keep_last=keep_last)
            logger.info("Snapshot criptografado gerado e verificado: %s", out_file.name)
            return out_file
        except Exception as exc:
            logger.error("Falha ao criar snapshot criptografado: %s", exc)
            return None
        finally:
            tmp_path.unlink(missing_ok=True)

    def _prune_old_snapshots(self, keep_last: int = 14) -> None:
        items = sorted(self.backups_dir.glob("auto_db_*.db.enc"), key=lambda p: p.stat().st_mtime)
        if len(items) > keep_last:
            for old in items[: len(items) - keep_last]:
                old.unlink(missing_ok=True)
