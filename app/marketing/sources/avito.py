"""Парсер Avito Услуги — поиск объявлений «нужен …»."""

import logging
from urllib.parse import quote

from .base import RawLead, Source
from .playwright_utils import browser_page

log = logging.getLogger("marketing.avito")

# Раздел «Предложения услуг / Запросы» — поиск по фразе
SEARCH_URL = "https://www.avito.ru/rossiya/predlozheniya_uslug?q={query}"
ITEM_SELECTOR = "[data-marker='item']"


class AvitoSource(Source):
    name = "avito"
    can_reply = False  # у Avito агрессивная антибот-защита; только сбор лидов

    async def fetch(self, queries: list[str]) -> list[RawLead]:
        leads: list[RawLead] = []
        async with browser_page() as page:
            if page is None:
                return leads
            for query in queries[:4]:
                try:
                    await page.goto(
                        SEARCH_URL.format(query=quote(query)),
                        wait_until="domcontentloaded",
                        timeout=20000,
                    )
                    await page.wait_for_timeout(2000)
                    cards = await page.query_selector_all(ITEM_SELECTOR)
                    for card in cards[:15]:
                        lead = await self._parse_card(card, query)
                        if lead is not None:
                            leads.append(lead)
                except Exception as exc:
                    log.debug("Avito поиск «%s» не удался: %s", query, exc)
        return leads

    async def _parse_card(self, card, query: str) -> RawLead | None:
        try:
            iid = await card.get_attribute("data-item-id")
            link = await card.query_selector("a[itemprop='url'], a")
            href = await link.get_attribute("href") if link else None
            title_el = await card.query_selector("[itemprop='name'], h3, a")
            title = (await title_el.inner_text()).strip() if title_el else ""
            if not title:
                return None
            external_id = iid or href or title
            url = href if href and href.startswith("http") else (
                f"https://www.avito.ru{href}" if href else None
            )
            return RawLead(self.name, external_id, title, url, None)
        except Exception:
            return None
