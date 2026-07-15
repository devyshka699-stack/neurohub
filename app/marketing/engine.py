"""Планировщик авто-маркетинга: раз в N минут собирает лиды и формирует отклики.

Безопасность:
- MARKETING_ENABLED=0 (по умолчанию) — движок не запускается вовсе.
- MARKETING_DRY_RUN=1 (по умолчанию) — отклики только формируются и сохраняются
  в БД, но НЕ отправляются на площадки. Это защищает аккаунты от банов и
  соблюдает правила площадок. Реальная отправка требует осознанного включения
  и поддержки со стороны конкретного источника (Source.can_reply).
- Антиспам: не более одного отклика на площадку за MARKETING_MIN_REPLY_INTERVAL.
"""

import asyncio
import logging
from datetime import datetime

from sqlalchemy.exc import IntegrityError

from .. import config
from ..database import SessionLocal
from ..models import (
    LEAD_FAILED,
    LEAD_NEW,
    LEAD_REPLIED,
    LEAD_REPLY_READY,
    LEAD_SKIPPED,
    Lead,
)
from . import keywords, limiter, templates
from .sources import ALL_SOURCES

log = logging.getLogger("marketing.engine")

_task: asyncio.Task | None = None
_source_by_name = {s.name: s for s in ALL_SOURCES}


def start() -> None:
    global _task
    if not config.MARKETING_ENABLED:
        log.info("Auto-marketing выключен (MARKETING_ENABLED=0)")
        return
    if _task is None or _task.done():
        _task = asyncio.get_event_loop().create_task(_loop())


async def _loop() -> None:
    mode = "DRY-RUN" if config.MARKETING_DRY_RUN else "LIVE"
    log.info(
        "Auto-marketing запущен (%s), проверка каждые %s с",
        mode, config.MARKETING_INTERVAL,
    )
    while True:
        try:
            await run_cycle()
        except Exception:
            log.exception("Ошибка цикла авто-маркетинга")
        await asyncio.sleep(config.MARKETING_INTERVAL)


async def run_cycle() -> dict:
    """Один проход: собрать лиды, сматчить, сформировать и (опц.) отправить отклики."""
    queries = keywords.all_search_queries()
    found = 0
    new_leads = 0

    for source in ALL_SOURCES:
        try:
            raw_leads = await source.fetch(queries)
        except Exception:
            log.exception("Источник %s упал", source.name)
            continue
        found += len(raw_leads)
        for raw in raw_leads:
            if _save_lead(raw):
                new_leads += 1

    replied = await _process_pending()
    log.info(
        "Цикл маркетинга: найдено %s, новых %s, откликов %s",
        found, new_leads, replied,
    )
    return {"found": found, "new": new_leads, "replied": replied}


def _save_lead(raw) -> bool:
    """Сохраняет лид, если он новый и совпал по ключевым словам. True, если добавлен."""
    text = f"{raw.title} {raw.snippet or ''}"
    task, keyword = keywords.match(text)
    if task is None:
        return False

    with SessionLocal() as db:
        lead = Lead(
            source=raw.source,
            external_id=str(raw.external_id),
            url=raw.url,
            title=raw.title[:2000],
            snippet=(raw.snippet or "")[:2000] or None,
            matched_task=task,
            matched_keyword=keyword,
            status=LEAD_NEW,
            dry_run=config.MARKETING_DRY_RUN,
        )
        db.add(lead)
        try:
            db.commit()
            return True
        except IntegrityError:
            db.rollback()  # дубликат (source, external_id) — уже видели
            return False


async def _process_pending() -> int:
    """Формирует отклики для новых лидов и отправляет, если разрешено."""
    replied = 0
    with SessionLocal() as db:
        pending = (
            db.query(Lead)
            .filter(Lead.status.in_([LEAD_NEW, LEAD_REPLY_READY]))
            .order_by(Lead.created_at.asc())
            .all()
        )

    for lead_stub in pending:
        with SessionLocal() as db:
            lead = db.get(Lead, lead_stub.id)
            if lead is None:
                continue

            if not lead.reply_text:
                lead.reply_text = templates.build_reply(lead.matched_task, lead.source)
                lead.status = LEAD_REPLY_READY
                db.commit()

            source = _source_by_name.get(lead.source)

            # dry-run или источник не умеет отправлять — оставляем «отклик готов»
            if config.MARKETING_DRY_RUN or source is None or not source.can_reply:
                continue

            # антиспам: не чаще одного отклика на площадку за интервал
            if not limiter.can_reply(lead.source):
                lead.status = LEAD_SKIPPED
                lead.error = "антиспам-лимит площадки"
                db.commit()
                continue

            try:
                await source.send_reply(lead.external_id, lead.reply_text)
                limiter.mark_replied(lead.source)
                lead.status = LEAD_REPLIED
                lead.replied_at = datetime.utcnow()
                lead.error = None
                replied += 1
            except Exception as exc:
                lead.status = LEAD_FAILED
                lead.error = str(exc)
            db.commit()

    return replied
