"""Парсер YouDo — лента открытых заданий."""

import logging

from .base import RawLead, Source
from .playwright_utils import browser_page

log = logging.getLogger("marketing.youdo")

# Публичная лента заданий, раздел «Виртуальный помощник / Дизайн / Тексты»
SEARCH_URL = "https://youdo.com/tasks-all-opened-all/"
ITEM_SELECTOR = "[data-task-id], .TasksList_task__item"


class YoudoSource(Source):
    name = "youdo"
    can_reply = False

    _base_url = SEARCH_URL

    async def fetch(self, queries: list[str]) -> list[RawLead]:
        leads: list[RawLead] = []
        async with browser_page() as page:
            if page is None:
                return leads
            try:
                await page.goto(self._base_url, wait_until="domcontentloaded", timeout=20000)
                await page.wait_for_timeout(2000)
                cards = await page.query_selector_all(ITEM_SELECTOR)
                for card in cards[:40]:
                    lead = await self._parse_card(card)
                    if lead is not None:
                        leads.append(lead)
            except Exception as exc:
                log.debug("%s лента не загрузилась: %s", self.name, exc)
        return leads

    async def _parse_card(self, card) -> RawLead | None:
        try:
            tid = await card.get_attribute("data-task-id")
            link = await card.query_selector("a")
            href = await link.get_attribute("href") if link else None
            title_el = await card.query_selector("a, h2, .TaskCard_title__link")
            title = (await title_el.inner_text()).strip() if title_el else ""
            if not title:
                return None
            external_id = tid or href or title
            return RawLead(self.name, external_id, title, href, None)
        except Exception:
            return None
