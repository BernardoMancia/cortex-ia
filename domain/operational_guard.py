from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from domain.b3_rules import BRT, get_brt_now, next_b3_session_open
from domain.models import GlobalOperationalMode, WalletOperationalMode

PANIC_PRESET_MINUTES: dict[str, int | None] = {
    "15m": 15,
    "30m": 30,
    "1h": 60,
    "2h": 120,
    "4h": 240,
    "indefinite": None,
}


def parse_iso_brt(raw: str | None) -> datetime | None:
    if not raw or not str(raw).strip():
        return None
    try:
        dt = datetime.fromisoformat(str(raw).strip())
        return dt.replace(tzinfo=BRT) if dt.tzinfo is None else dt.astimezone(BRT)
    except ValueError:
        return None


def resolve_panic_until(
    duration_preset: str = "indefinite",
    custom_minutes: int | None = None,
    now: datetime | None = None,
) -> str | None:
    ref = now or get_brt_now()
    preset = (duration_preset or "indefinite").strip().lower()
    if custom_minutes is not None and int(custom_minutes) > 0:
        capped = min(int(custom_minutes), 60 * 24 * 30)
        return (ref + timedelta(minutes=capped)).isoformat()
    if preset == "next_session":
        return next_b3_session_open(ref).isoformat()
    if preset in PANIC_PRESET_MINUTES:
        mins = PANIC_PRESET_MINUTES[preset]
        if mins is None:
            return None
        return (ref + timedelta(minutes=mins)).isoformat()
    return None


@dataclass(frozen=True)
class ExecutionGateStatus:
    can_execute_orders: bool
    can_observe_signals: bool
    effective_mode: str
    block_reason: str
    remaining_seconds: int | None = None


class OperationalGuard:
    @staticmethod
    def evaluate_global(global_state: dict[str, Any], now: datetime | None = None) -> ExecutionGateStatus:
        ref = now or get_brt_now()
        mode = str(global_state.get("global_mode") or GlobalOperationalMode.ACTIVE.value).upper()
        if mode == GlobalOperationalMode.MAINTENANCE.value:
            reason = str(global_state.get("maintenance_reason") or "Servidor em Modo Manutenção Pós-Boot.")
            return ExecutionGateStatus(
                can_execute_orders=False,
                can_observe_signals=True,
                effective_mode=GlobalOperationalMode.MAINTENANCE.value,
                block_reason=reason,
                remaining_seconds=None,
            )
        if mode == GlobalOperationalMode.PANIC.value:
            until_dt = parse_iso_brt(global_state.get("panic_until"))
            if until_dt is not None and ref >= until_dt:
                return ExecutionGateStatus(
                    can_execute_orders=True,
                    can_observe_signals=True,
                    effective_mode=GlobalOperationalMode.ACTIVE.value,
                    block_reason="",
                    remaining_seconds=0,
                )
            rem = int((until_dt - ref).total_seconds()) if until_dt is not None else None
            reason = str(global_state.get("panic_reason") or "Pânico Global ativo no servidor.")
            return ExecutionGateStatus(
                can_execute_orders=False,
                can_observe_signals=False,
                effective_mode=GlobalOperationalMode.PANIC.value,
                block_reason=reason,
                remaining_seconds=rem,
            )
        return ExecutionGateStatus(
            can_execute_orders=True,
            can_observe_signals=True,
            effective_mode=GlobalOperationalMode.ACTIVE.value,
            block_reason="",
            remaining_seconds=None,
        )

    @staticmethod
    def evaluate_wallet(
        global_state: dict[str, Any],
        wallet: dict[str, Any],
        now: datetime | None = None,
    ) -> ExecutionGateStatus:
        ref = now or get_brt_now()
        g_status = OperationalGuard.evaluate_global(global_state, now=ref)
        if not g_status.can_execute_orders:
            return g_status

        w_mode = str(wallet.get("operational_mode") or WalletOperationalMode.PAUSED_UNCONFIGURED.value).upper()
        if w_mode == WalletOperationalMode.PANIC.value:
            until_dt = parse_iso_brt(wallet.get("panic_until"))
            if until_dt is None or ref < until_dt:
                rem = int((until_dt - ref).total_seconds()) if until_dt is not None else None
                return ExecutionGateStatus(
                    can_execute_orders=False,
                    can_observe_signals=False,
                    effective_mode=WalletOperationalMode.PANIC.value,
                    block_reason=str(wallet.get("panic_reason") or "Botão de Pânico ativo nesta carteira."),
                    remaining_seconds=rem,
                )
            w_mode = WalletOperationalMode.ACTIVE.value

        if not bool(wallet.get("automation_enabled", 0)):
            return ExecutionGateStatus(
                can_execute_orders=False,
                can_observe_signals=True,
                effective_mode="AUTOMATION_OFF",
                block_reason="Automação desativada pelo usuário nesta carteira.",
            )

        if w_mode == WalletOperationalMode.OBSERVE_ONLY.value:
            return ExecutionGateStatus(
                can_execute_orders=False,
                can_observe_signals=True,
                effective_mode=WalletOperationalMode.OBSERVE_ONLY.value,
                block_reason="Carteira em Modo Apenas Monitorar (sem envio de ordens).",
            )

        if w_mode in (
            WalletOperationalMode.PAUSED_UNCONFIGURED.value,
            WalletOperationalMode.PAUSED_BROKER_ERROR.value,
        ):
            return ExecutionGateStatus(
                can_execute_orders=False,
                can_observe_signals=False,
                effective_mode=w_mode,
                block_reason="Carteira aguardando configuração ou reconciliação da corretora.",
            )

        return ExecutionGateStatus(
            can_execute_orders=True,
            can_observe_signals=True,
            effective_mode=WalletOperationalMode.ACTIVE.value,
            block_reason="",
        )
