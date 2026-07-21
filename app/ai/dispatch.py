"""Диспетчер AI: локальная очередь или удалённый воркер на Маке."""

from sqlalchemy.orm import Session

from .. import config
from ..models import Order
from . import queue as ai_queue


def schedule_ai(order: Order, db: Session) -> None:
    """Ставит заказ на AI-обработку.

    REMOTE_WORKER=1 (Render): только помечает queued — заберёт воркер с Мака.
    Иначе: локальная asyncio-очередь.
    """
    if config.REMOTE_WORKER:
        order.ai_status = ai_queue.AI_QUEUED
        order.ai_error = None
        db.commit()
    else:
        ai_queue.enqueue(order, db)
