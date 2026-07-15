"""Планировщик рассылок удержания: win-back и ежемесячная рассылка."""

import asyncio
import logging
from datetime import datetime, timedelta

from .. import config
from ..database import SessionLocal
from ..models import (
    PROMO_WINBACK,
    STATUS_DONE,
    STATUS_IN_PROGRESS,
    Order,
    User,
)
from . import messages, promo
from .service import notify

log = logging.getLogger("retention.scheduler")

_task: asyncio.Task | None = None


def start() -> None:
    global _task
    if not config.RETENTION_ENABLED:
        log.info("Система удержания выключена (RETENTION_ENABLED=0)")
        return
    if _task is None or _task.done():
        _task = asyncio.get_event_loop().create_task(_loop())


async def _loop() -> None:
    log.info(
        "Планировщик удержания запущен (проверка каждые %s с)",
        config.RETENTION_INTERVAL,
    )
    while True:
        try:
            await asyncio.to_thread(run_cycle)
        except Exception:
            log.exception("Ошибка цикла удержания")
        await asyncio.sleep(config.RETENTION_INTERVAL)


def run_cycle() -> dict:
    """Один проход: win-back неактивным + ежемесячная рассылка. Возвращает счётчики."""
    winback = _run_winback()
    newsletter = _run_newsletter()
    log.info("Цикл удержания: win-back %s, рассылка %s", winback, newsletter)
    return {"winback": winback, "newsletter": newsletter}


def _last_paid_order_date(db, user: User) -> datetime | None:
    order = (
        db.query(Order)
        .filter(
            Order.user_id == user.id,
            Order.status.in_([STATUS_IN_PROGRESS, STATUS_DONE]),
        )
        .order_by(Order.created_at.desc())
        .first()
    )
    return order.created_at if order else None


def _run_winback() -> int:
    now = datetime.utcnow()
    threshold = now - timedelta(days=config.WINBACK_DAYS)
    cooldown = now - timedelta(days=config.WINBACK_COOLDOWN_DAYS)
    sent = 0

    with SessionLocal() as db:
        users = db.query(User).filter(User.is_admin.is_(False)).all()
        for user in users:
            last_order = _last_paid_order_date(db, user)
            if last_order is None or last_order > threshold:
                continue  # никогда не платил или заказывал недавно
            if user.last_winback_at and user.last_winback_at > cooldown:
                continue  # уже недавно писали
            code = promo.create_promo(
                db,
                user_id=user.id,
                discount_percent=config.WINBACK_PERCENT,
                reason=PROMO_WINBACK,
                valid_hours=72,
                prefix="BACK",
            )
            notify(db, user, messages.winback(code.code), kind="winback")
            user.last_winback_at = now
            db.commit()
            sent += 1
    return sent


def _run_newsletter() -> int:
    now = datetime.utcnow()
    threshold = now - timedelta(days=config.NEWSLETTER_EVERY_DAYS)
    sent = 0

    with SessionLocal() as db:
        users = db.query(User).filter(User.is_admin.is_(False)).all()
        for user in users:
            if user.last_newsletter_at and user.last_newsletter_at > threshold:
                continue
            # рассылка только тем, кто хоть раз что-то заказывал
            if user.paid_orders_count == 0:
                continue
            notify(db, user, messages.newsletter(), kind="newsletter")
            user.last_newsletter_at = now
            db.commit()
            sent += 1
    return sent
