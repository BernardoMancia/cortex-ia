from __future__ import annotations

from typing import Any

__all__ = [
    "CortexOrchestrator",
    "CustodyReconciler",
    "CycleScheduler",
    "PanicService",
    "TelegramCommandRouter",
    "WalletCycleRunner",
]


def __getattr__(name: str) -> Any:
    if name == "CycleScheduler":
        from engine.scheduler import CycleScheduler
        return CycleScheduler
    if name == "CustodyReconciler":
        from engine.custody_sync import CustodyReconciler
        return CustodyReconciler
    if name == "PanicService":
        from engine.panic_service import PanicService
        return PanicService
    if name == "WalletCycleRunner":
        from engine.wallet_cycle import WalletCycleRunner
        return WalletCycleRunner
    if name == "TelegramCommandRouter":
        from engine.telegram_commands import TelegramCommandRouter
        return TelegramCommandRouter
    if name == "CortexOrchestrator":
        from engine.orchestrator import CortexOrchestrator
        return CortexOrchestrator
    raise AttributeError(f"module 'engine' has no attribute {name!r}")
