"""Базовые типы источников лидов."""

import logging
from dataclasses import dataclass

log = logging.getLogger("marketing.sources")


@dataclass
class RawLead:
    source: str
    external_id: str
    title: str
    url: str | None = None
    snippet: str | None = None


class Source:
    """Базовый источник. Наследники реализуют fetch()."""

    name: str = "base"
    # Можно ли реально отправлять отклик через этот источник (иначе только лид).
    can_reply: bool = False

    async def fetch(self, queries: list[str]) -> list[RawLead]:
        """Возвращает найденные лиды. Не должен бросать исключения наружу."""
        raise NotImplementedError

    async def send_reply(self, external_id: str, text: str) -> None:
        """Отправляет отклик. Вызывается только если can_reply и не dry-run."""
        raise NotImplementedError(
            f"Источник {self.name} не поддерживает автоотправку откликов"
        )
