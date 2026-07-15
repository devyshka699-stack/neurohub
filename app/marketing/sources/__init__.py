"""Источники лидов: фриланс-биржи и чаты/соцсети.

Каждый источник возвращает список RawLead. Парсеры бирж используют Playwright;
если Playwright/страница недоступны, источник возвращает пустой список и не
роняет планировщик.
"""

from .base import RawLead, Source
from .avito import AvitoSource
from .kwork import KworkSource
from .telegram_chats import TelegramChatsSource
from .youdo import YoudoSource

# WorkZilla устроена как YouDo (лента заданий) — используем общий парсер с другим URL.
from .workzilla import WorkzillaSource

ALL_SOURCES: list[Source] = [
    KworkSource(),
    WorkzillaSource(),
    YoudoSource(),
    AvitoSource(),
    TelegramChatsSource(),
]

__all__ = ["RawLead", "Source", "ALL_SOURCES"]
