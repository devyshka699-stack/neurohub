"""Парсер публичных Telegram-чатов через веб-превью (t.me/s/<канал>).

Это самый безопасный способ мониторинга Telegram: читает публичную веб-версию
канала/чата без авторизации и без клиентского API. Список чатов задаётся в
MARKETING_TG_CHATS (переменная окружения, каналы через запятую).

Соцсети (VK/Instagram и т.п.) сюда же не включены как отдельные парсеры:
их публичный доступ закрыт авторизацией и меняющейся защитой, поэтому
для соцсетей используется тот же механизм лидов, но источники добавляются
по мере получения доступа. Здесь реализован рабочий Telegram-парсер.
"""

import logging
import os

from .base import RawLead, Source
from .playwright_utils import browser_page

log = logging.getLogger("marketing.telegram")


def _chats() -> list[str]:
    raw = os.getenv("MARKETING_TG_CHATS", "")
    return [c.strip().lstrip("@") for c in raw.split(",") if c.strip()]


class TelegramChatsSource(Source):
    name = "telegram"
    can_reply = False  # автоответ в чужие чаты требует членства и легко ловит бан

    async def fetch(self, queries: list[str]) -> list[RawLead]:
        chats = _chats()
        if not chats:
            return []
        leads: list[RawLead] = []
        async with browser_page() as page:
            if page is None:
                return leads
            for chat in chats:
                try:
                    await page.goto(
                        f"https://t.me/s/{chat}",
                        wait_until="domcontentloaded",
                        timeout=20000,
                    )
                    await page.wait_for_timeout(1200)
                    msgs = await page.query_selector_all(".tgme_widget_message")
                    for msg in msgs[-30:]:
                        lead = await self._parse_msg(msg, chat)
                        if lead is not None:
                            leads.append(lead)
                except Exception as exc:
                    log.debug("Telegram-чат @%s не прочитан: %s", chat, exc)
        return leads

    async def _parse_msg(self, msg, chat: str) -> RawLead | None:
        try:
            text_el = await msg.query_selector(".tgme_widget_message_text")
            text = (await text_el.inner_text()).strip() if text_el else ""
            if not text:
                return None
            data_post = await msg.get_attribute("data-post")  # "chat/123"
            external_id = data_post or text[:64]
            url = f"https://t.me/{data_post}" if data_post else f"https://t.me/{chat}"
            title = text[:120]
            return RawLead(self.name, external_id, title, url, text)
        except Exception:
            return None
