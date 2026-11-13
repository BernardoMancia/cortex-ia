from __future__ import annotations

import time
from typing import Any

import psutil

_BOOT_MONO = time.monotonic()


class SystemHealthMonitor:
    @staticmethod
    def collect_metrics() -> dict[str, Any]:
        try:
            cpu = float(psutil.cpu_percent(interval=None))
            mem = float(psutil.virtual_memory().percent)
            disk = float(psutil.disk_usage("/").percent)
        except Exception:
            cpu, mem, disk = 0.0, 0.0, 0.0
        return {
            "cpu_percent": round(cpu, 1),
            "ram_percent": round(mem, 1),
            "disk_percent": round(disk, 1),
            "uptime_seconds": round(time.monotonic() - _BOOT_MONO, 1),
        }
