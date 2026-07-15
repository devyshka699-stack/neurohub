"""Парсер биржи Kwork (раздел «Кворк体» → биржа проектов).

Kwork требует авторизацию для полного доступа к бирже; публично доступен
каталог проектов. Селекторы вынесены в константы — при изменении вёрстки
правятся в одном месте.
"""

import logging
from urllib.parse import quote

from .base import RawLead, Source
from .playwright_utils import browser_page

log = logging.getLogger("marketing.kwork")

SEARCH_URL = "https://kwork.ru/projects?keyword={query}"
ITEM_SELECTOR = "div.want-card, article[data-project-id]"


class KworkSource(Source):
    name = "kwork"
    can_reply = False  # отклик требует авторизации и оплаченного доступа к бирже

    async def fetch(self, queries: list[str]) -> list[RawLead]:
        leads: list[RawLead] = []
        # На бирже проектов один общий поиск — берём несколько ключевых фраз
        async with browser_page() as page:
            if page is None:
                return leads
            for query in queries[:5]:
                try:
                    await page.goto(
                        SEARCH_URL.format(query=quote(query)),
                        wait_until="domcontentloaded",
                        timeout=20000,
                    )
                    await page.wait_for_timeout(1500)
                    cards = await page.query_selector_all(ITEM_SELECTOR)
                    for card in cards[:20]:
                        lead = await self._parse_card(card, query)
                        if lead is not None:
                            leads.append(lead)
                except Exception as exc:
                    log.debug("Kwork поиск «%s» не удался: %s", query, exc)
        return leads

    async def _parse_card(self, card, query: str) -> RawLead | None:
        try:
            pid = await card.get_attribute("data-project-id")
            link = await card.query_selector("a")
            href = await link.get_attribute("href") if link else None
            title_el = await card.query_selector("a, h1, .wants-card__header-title")
            title = (await title_el.inner_text()).strip() if title_el else ""
            desc_el = await card.query_selector(".want-card__description, p")
            snippet = (await desc_el.inner_text()).strip() if desc_el else None
            if not title:
                return None
            external_id = pid or href or title
            url = href if href and href.startswith("http") else (
                f"https://kwork.ru{href}" if href else None
            )
            return RawLead(self.name, external_id, title, url, snippet)
        except Exception:
            return None
