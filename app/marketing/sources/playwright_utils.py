"""Обёртка над Playwright с мягкой деградацией.

Если Playwright не установлен или браузер не запускается — возвращаем None,
парсер отдаёт пустой список, планировщик продолжает работу.
"""

import logging
from contextlib import asynccontextmanager

log = logging.getLogger("marketing.playwright")

_WARNED = False


@asynccontextmanager
async def browser_page(user_agent: str | None = None):
    """Асинхронный контекст: (page) или (None), если Playwright недоступен."""
    global _WARNED
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        if not _WARNED:
            log.warning(
                "Playwright не установлен. Установите: pip install playwright && "
                "playwright install chromium. Парсеры бирж отключены."
            )
            _WARNED = True
        yield None
        return

    pw = None
    browser = None
    try:
        pw = await async_playwright().start()
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=user_agent
            or (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="ru-RU",
        )
        page = await context.new_page()
        yield page
    except Exception as exc:
        log.warning("Playwright не смог запустить браузер: %s", exc)
        yield None
    finally:
        if browser is not None:
            await browser.close()
        if pw is not None:
            await pw.stop()
