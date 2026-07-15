"""Парсеры фриланс-бирж (Playwright) и соцсетей (VK API).

Каждый парсер возвращает список FoundLead. Ошибки не роняют планировщик —
логируются и возвращается пустой список.
"""

import logging
from dataclasses import dataclass

import httpx

from .. import config
from .keywords import all_search_phrases, detect_lead_task

log = logging.getLogger("marketing.parsers")

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


@dataclass
class FoundLead:
    platform: str
    external_id: str
    url: str
    title: str
    text: str


async def _fetch_rendered(url: str, wait_selector: str | None = None) -> str:
    """Открывает страницу в headless-Chromium и возвращает HTML."""
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            page = await browser.new_page(user_agent=_UA, locale="ru-RU")
            await page.goto(url, timeout=45000, wait_until="domcontentloaded")
            if wait_selector:
                try:
                    await page.wait_for_selector(wait_selector, timeout=10000)
                except Exception:
                    pass  # разметка могла поменяться — парсим что есть
            return await page.content()
        finally:
            await browser.close()


async def parse_kwork() -> list[FoundLead]:
    """Биржа заказов Kwork (публичная страница «Биржа»)."""
    if not config.KWORK_ENABLED:
        return []
    try:
        html = await _fetch_rendered("https://kwork.ru/projects", ".want-card")
        return _extract_generic(
            html, platform="kwork", base_url="https://kwork.ru",
            link_substr="/projects/",
        )
    except Exception as exc:
        log.warning("Kwork: не удалось получить заказы: %s", exc)
        return []


async def parse_youdo() -> list[FoundLead]:
    """Биржа заданий YouDo (публичный список задач)."""
    if not config.YOUDO_ENABLED:
        return []
    try:
        html = await _fetch_rendered("https://youdo.com/tasks", "a[href*='/t']")
        return _extract_generic(
            html, platform="youdo", base_url="https://youdo.com",
            link_substr="/t",
        )
    except Exception as exc:
        log.warning("YouDo: не удалось получить задания: %s", exc)
        return []


def _extract_generic(
    html: str, platform: str, base_url: str, link_substr: str
) -> list[FoundLead]:
    """Достаёт из HTML все ссылки на заказы и фильтрует по ключевым словам.

    Разметка бирж часто меняется, поэтому парсим устойчиво: берём все <a>
    с подходящим href и текстом, а тематику определяем по ключевым фразам.
    """
    from html.parser import HTMLParser

    class LinkParser(HTMLParser):
        def __init__(self):
            super().__init__()
            self.links: list[tuple[str, str]] = []
            self._href: str | None = None
            self._buf: list[str] = []

        def handle_starttag(self, tag, attrs):
            if tag == "a":
                href = dict(attrs).get("href", "")
                if link_substr in href:
                    self._href = href
                    self._buf = []

        def handle_data(self, data):
            if self._href is not None:
                self._buf.append(data)

        def handle_endtag(self, tag):
            if tag == "a" and self._href is not None:
                text = " ".join("".join(self._buf).split())
                if len(text) > 15:
                    self.links.append((self._href, text))
                self._href = None

    parser = LinkParser()
    parser.feed(html)

    leads, seen = [], set()
    for href, text in parser.links:
        task = detect_lead_task(text)
        if task is None:
            continue
        url = href if href.startswith("http") else base_url + href
        external_id = url.split("?")[0].rstrip("/").rsplit("/", 1)[-1]
        if external_id in seen:
            continue
        seen.add(external_id)
        leads.append(FoundLead(
            platform=platform, external_id=external_id, url=url,
            title=text[:200], text=text,
        ))
    log.info("%s: найдено лидов по ключевым словам: %d", platform, len(leads))
    return leads


async def parse_vk() -> list[FoundLead]:
    """Поиск свежих постов ВКонтакте по ключевым фразам (VK API newsfeed.search)."""
    if not config.VK_TOKEN:
        return []
    leads = []
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            for phrase in all_search_phrases():
                resp = await client.get(
                    "https://api.vk.com/method/newsfeed.search",
                    params={
                        "q": phrase,
                        "count": 20,
                        "access_token": config.VK_TOKEN,
                        "v": "5.199",
                    },
                )
                data = resp.json()
                for item in data.get("response", {}).get("items", []):
                    text = item.get("text", "")
                    task = detect_lead_task(text)
                    if task is None:
                        continue
                    owner, post = item["owner_id"], item["id"]
                    leads.append(FoundLead(
                        platform="vk",
                        external_id=f"{owner}_{post}",
                        url=f"https://vk.com/wall{owner}_{post}",
                        title=text[:200],
                        text=text[:2000],
                    ))
    except Exception as exc:
        log.warning("VK: ошибка поиска: %s", exc)
    return leads


ALL_PARSERS = [parse_kwork, parse_youdo, parse_vk]
