from __future__ import annotations

from typing import Any, Callable
import requests

from config.settings import settings
from observability.logger import get_logger

logger = get_logger("telegram")


class TelegramClient:
    def __init__(
        self,
        token: str | None = None,
        admin_chat_id: str | None = None,
        admin_user_ids: frozenset[int] | None = None,
    ) -> None:
        self.token = (token if token is not None else settings.TELEGRAM_TOKEN).strip()
        self.admin_chat_id = (admin_chat_id if admin_chat_id is not None else settings.TELEGRAM_CHAT_ID).strip()
        self.admin_user_ids = (
            admin_user_ids if admin_user_ids is not None else settings.TELEGRAM_ADMIN_USER_IDS
        )
        self._last_update_id: int = 0
        self._session = requests.Session()

    @property
    def enabled(self) -> bool:
        return bool(self.token and self.admin_chat_id)

    def send_admin_alert(self, message: str) -> bool:
        if not self.enabled:
            return False
        return self.send_to_chat(self.admin_chat_id, message)

    def send_wallet_notification(self, wallet_chat_id: str | None, message: str) -> bool:
        target = (wallet_chat_id or "").strip()
        if not self.token or not target:
            return False
        return self.send_to_chat(target, message)

    def send_to_chat(self, chat_id: str, text: str) -> bool:
        if not self.token or not chat_id or not text:
            return False
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        try:
            resp = self._session.post(
                url,
                json={
                    "chat_id": chat_id,
                    "text": text[:4000],
                    "disable_web_page_preview": True,
                },
                timeout=10,
            )
            return resp.status_code == 200
        except Exception as exc:
            logger.warning("Falha de envio Telegram: %s", exc)
            return False

    def is_authorized_luke_sender(self, update: dict[str, Any]) -> bool:
        if "channel_post" in update or "edited_channel_post" in update:
            return False
        msg = update.get("message")
        if not isinstance(msg, dict):
            return False
        sender = msg.get("from")
        chat = msg.get("chat")
        if not isinstance(sender, dict) or not isinstance(chat, dict):
            return False
        if bool(sender.get("is_bot", False)):
            return False
        sender_id = sender.get("id")
        if not isinstance(sender_id, int) or sender_id <= 0:
            return False
        if not self.admin_user_ids or sender_id not in self.admin_user_ids:
            return False
        if str(chat.get("type", "")) != "private":
            return False
        if self.admin_chat_id and str(chat.get("id", "")) != self.admin_chat_id:
            return False
        return True

    def poll_authorized_commands(
        self,
        handler: Callable[[str, list[str]], str | None],
    ) -> int:
        if not self.enabled or not self.admin_user_ids:
            return 0
        url = f"https://api.telegram.org/bot{self.token}/getUpdates"
        params: dict[str, Any] = {
            "timeout": 0,
            "allowed_updates": ["message"],
        }
        if self._last_update_id > 0:
            params["offset"] = self._last_update_id + 1
        try:
            resp = self._session.get(url, params=params, timeout=8)
            if resp.status_code != 200:
                return 0
            body = resp.json()
            updates = body.get("result", []) if isinstance(body, dict) else []
        except Exception:
            return 0

        processed = 0
        for upd in updates:
            if not isinstance(upd, dict):
                continue
            uid = int(upd.get("update_id", 0) or 0)
            if uid > self._last_update_id:
                self._last_update_id = uid
            if not self.is_authorized_luke_sender(upd):
                continue
            text = str(upd.get("message", {}).get("text", "") or "").strip()
            if not text.startswith("/"):
                continue
            parts = text.split()
            cmd = parts[0].split("@")[0].lower()
            reply = handler(cmd, parts[1:])
            if reply:
                self.send_admin_alert(reply)
            processed += 1
        return processed

    def close(self) -> None:
        try:
            self._session.close()
        except Exception:
            pass
