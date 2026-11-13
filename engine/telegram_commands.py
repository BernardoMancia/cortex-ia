from __future__ import annotations

from typing import TYPE_CHECKING, Any

from config.settings import OWNER_USER_ID, OWNER_USERNAME, settings
from domain.b3_rules import format_brl
from domain.models import GlobalOperationalMode
from domain.operational_guard import PANIC_PRESET_MINUTES
from observability.health import SystemHealthMonitor
from storage.connection import DatabaseConnection
from storage.state_repo import StateRepository
from storage.trading_repo import TradingRepository
from storage.wallets_repo import WalletsRepository

if TYPE_CHECKING:
    from engine.panic_service import PanicService


class TelegramCommandRouter:
    def __init__(
        self,
        db: DatabaseConnection | None = None,
        panic_service: PanicService | None = None,
    ) -> None:
        self.db = db or DatabaseConnection()
        self.state_repo = StateRepository(self.db)
        self.wallets_repo = WalletsRepository(self.db)
        self.trading_repo = TradingRepository(self.db)
        self._panic_service = panic_service

    @property
    def panic_service(self) -> PanicService:
        if self._panic_service is None:
            from engine.panic_service import PanicService

            self._panic_service = PanicService(db=self.db)
        return self._panic_service

    def __call__(self, cmd: str, args: list[str] | None = None) -> str | None:
        return self.handle_command(cmd, args)

    def handle_command(self, cmd: str, args: list[str] | None = None) -> str:
        clean_cmd = (cmd or "").strip().lower()
        tokens = list(args or [])

        if clean_cmd in ("/status", "/estado"):
            return self._build_status_reply()

        if clean_cmd in ("/panico", "/panic"):
            return self._handle_panic_command(tokens)

        if clean_cmd in ("/liberar", "/desbloquear", "/unlock", "/ativo"):
            return (
                f"[{settings.ENVIRONMENT_LABEL}] Operação bloqueada via Telegram.\n"
                "Por segurança máxima, a liberação do Modo MANUTENÇÃO (MAINTENANCE) na Produção "
                "exige acesso ao Dashboard Web com verificação 2FA Step-Up e Pre-Flight Check aprovado."
            )

        if clean_cmd in ("/ajuda", "/help", "/start"):
            return self._build_help_reply()

        return (
            f"Comando não reconhecido: {clean_cmd}\n"
            "Use /ajuda para listar os comandos autorizados do Luke."
        )

    def _build_status_reply(self) -> str:
        state = self.state_repo.get_state(auto_expire_panic=True)
        g_mode = str(state.get("global_mode") or GlobalOperationalMode.ACTIVE.value)
        health = SystemHealthMonitor.collect_metrics()
        wallet = self.wallets_repo.get_default_or_first_wallet(OWNER_USER_ID)

        lines = [
            f"CÓRTEX IA v{settings.VERSION} — {settings.ENVIRONMENT_LABEL}",
            f"Modo Global: {g_mode}",
        ]
        if g_mode == GlobalOperationalMode.MAINTENANCE.value:
            reason = str(state.get("maintenance_reason") or "Bloqueio pós-boot ativo.")
            lines.append(f"Motivo: {reason}")
            lines.append(
                "Aviso: Para desbloquear o modo MAINTENANCE na Produção, utilize o Dashboard Web com 2FA Step-Up."
            )
        elif g_mode == GlobalOperationalMode.PANIC.value:
            until = str(state.get("panic_until") or "indefinido")
            lines.append(f"Pânico até: {until}")

        if wallet is not None:
            wid = int(wallet["id"])
            positions = self.trading_repo.list_positions(OWNER_USER_ID, wid)
            pos_val = sum(p.market_value for p in positions)
            free_cash = float(wallet.get("free_cash", 0.0) or 0.0)
            vault = float(wallet.get("vault_reserve_cash", 0.0) or 0.0)
            lines.extend(
                [
                    f"Carteira: {wallet.get('name')} ({wallet.get('operational_mode')})",
                    f"Caixa Livre: {format_brl(free_cash)} | Cofre: {format_brl(vault)}",
                    f"Posições Abertas: {len(positions)} ({format_brl(pos_val)})",
                ]
            )

        lines.append(
            f"Saúde Servidor: CPU {health['cpu_percent']}% | RAM {health['ram_percent']}% | Disco {health['disk_percent']}%"
        )
        return "\n".join(lines)

    def _handle_panic_command(self, tokens: list[str]) -> str:
        preset = "indefinite"
        liquidate = False
        valid_presets = set(PANIC_PRESET_MINUTES.keys()) | {"next_session"}

        for tok in tokens:
            low = tok.strip().lower()
            if low in valid_presets:
                preset = low
            elif low in ("liquidar", "zerar", "liquidate"):
                liquidate = True

        res: dict[str, Any] = self.panic_service.activate_global_panic(
            actor_user_id=OWNER_USER_ID,
            actor_username=OWNER_USERNAME,
            duration_preset=preset,
            reason="Acionado diretamente pelo Luke via comando privado /panico no Telegram.",
            liquidate_positions=liquidate,
            ip_address="telegram_private",
        )
        until_label = res.get("panic_until") or "indefinido"
        liq_list = res.get("liquidated_tickers") or []
        prot_list = res.get("skipped_protected") or []
        parts = [
            f"PÂNICO GLOBAL ATIVADO ({settings.ENVIRONMENT_LABEL})",
            f"Duração: {preset} (até: {until_label})",
        ]
        if liquidate:
            parts.append(f"Posições liquidadas: {', '.join(liq_list) if liq_list else 'nenhuma'}")
            if prot_list:
                parts.append(f"Protegidas (do_not_touch): {', '.join(prot_list)}")
        return "\n".join(parts)

    @staticmethod
    def _build_help_reply() -> str:
        return (
            f"Comandos Privados Autorizados — {OWNER_USERNAME} ({settings.ENVIRONMENT_LABEL}):\n"
            "/status — Estado global, custódia da carteira principal e saúde do servidor\n"
            "/panico [15m|30m|1h|2h|4h|next_session|indefinite] [liquidar] — Ativa Pânico Global imediato\n"
            "/ajuda — Lista comandos e regras de segurança\n\n"
            "Nota de Segurança: O desbloqueio do Modo MAINTENANCE na Produção não é permitido via Telegram; "
            "utilize exclusivamente o Dashboard Web autenticado com 2FA Step-Up."
        )
