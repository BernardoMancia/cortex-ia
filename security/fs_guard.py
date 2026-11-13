from __future__ import annotations

import os
from pathlib import Path
import sys


def enforce_secure_permissions(
    data_dir: Path,
    keys_dir: Path,
    backups_dir: Path,
    project_root: Path,
) -> dict[str, str]:
    status_report: dict[str, str] = {}
    for directory in (data_dir, keys_dir, backups_dir):
        directory.mkdir(parents=True, exist_ok=True)
        if sys.platform != "win32":
            try:
                os.chmod(directory, 0o700)
                status_report[str(directory)] = "0700"
            except OSError:
                status_report[str(directory)] = "chmod_skipped"
        else:
            status_report[str(directory)] = "windows_acl"

    sensitive_files = [
        project_root / ".env",
        data_dir / "cortex.db",
        data_dir / "cortex.db-wal",
        data_dir / "cortex.db-shm",
    ]
    if keys_dir.exists():
        sensitive_files.extend(keys_dir.glob("*.key"))

    for file_path in sensitive_files:
        if file_path.exists() and file_path.is_file():
            if sys.platform != "win32":
                try:
                    os.chmod(file_path, 0o600)
                    status_report[str(file_path)] = "0600"
                except OSError:
                    status_report[str(file_path)] = "chmod_skipped"
            else:
                status_report[str(file_path)] = "windows_acl"
    return status_report
