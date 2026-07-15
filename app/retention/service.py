"""Ядро системы удержания: уведомления, реакции на события, начисление статусов."""

import logging
from datetime import datetime

from sqlalchemy.orm import Session

from .. import config
from ..database import SessionLocal
from ..models import (
    PROMO_POST_ORDER,
    STATUS_DONE,
    Notification,
    Order,
    User,
)
from . import messages, promo

log = logging.getLogger("retention")


def compute_order_pricing(
    db: Session, user: User, service, promo_code_str: str | None
) -> tuple[int, int, int, "PromoCode | None", str | None]:
    """Считает цену заказа со скидкой.

    Берётся лучшая из двух скидок (не суммируются): накопительная скидка клиента
    или скидка по промокоду. Возвращает
    (base_price, discount_percent, final_price, promo_or_None, error_or_None).
    """
    from ..models import PromoCode  # локальный импорт для аннотаций

    base_price = service.price
    loyalty = user.loyalty_discount

    promo_obj = None
    promo_discount = 0
    error = None
    if promo_code_str:
        code = promo_code_str.strip().upper()
        promo_obj = (
            db.query(PromoCode)
            .filter(PromoCode.code == code, PromoCode.user_id == user.id)
            .first()
        )
        if promo_obj is None:
            error = "Промокод не найден"
            promo_obj = None
        elif not promo_obj.is_valid():
            error = "Промокод недействителен или истёк"
            promo_obj = None
        else:
            promo_discount = promo_obj.discount_percent

    discount = max(loyalty, promo_discount)
    # промокод учитываем как применённый только если он реально дал лучшую скидку
    if promo_obj is not None and promo_discount < discount:
        promo_obj = None

    final_price = round(base_price * (100 - discount) / 100)
    return base_price, discount, final_price, promo_obj, error


def notify(db: Session, user: User, text: str, kind: str = "info") -> Notification:
    """Создаёт уведомление в кабинете и доставляет в Telegram, если возможно."""
    note = Notification(user_id=user.id, kind=kind, text=text)
    db.add(note)
    db.commit()

    if user.telegram_id:
        try:
            from .. import tgbot
            tgbot.schedule_message(user.telegram_id, text)
            note.delivered_tg = True
            db.commit()
        except Exception:
            log.exception("Не удалось доставить уведомление в Telegram")
    return note


def on_order_completed(order_id: int) -> None:
    """Хук: заказ выполнен. Выдаём промокод 30% и проверяем статусы лояльности."""
    with SessionLocal() as db:
        order = db.get(Order, order_id)
        if order is None or order.status != STATUS_DONE:
            return
        user = order.user

        # 1) промокод на следующий заказ
        code = promo.create_promo(
            db,
            user_id=user.id,
            discount_percent=config.PROMO_POST_ORDER_PERCENT,
            reason=PROMO_POST_ORDER,
            valid_hours=config.PROMO_POST_ORDER_HOURS,
        )
        notify(db, user, messages.post_order(code.code), kind="promo")

        # 2) проверка VIP и роста накопительной скидки
        _check_loyalty(db, user)


def _check_loyalty(db: Session, user: User) -> None:
    paid = user.paid_orders_count

    # VIP при достижении порога заказов
    if not user.is_vip and paid >= config.VIP_ORDERS_THRESHOLD:
        user.is_vip = True
        user.vip_since = datetime.utcnow()
        db.commit()
        notify(db, user, messages.vip_granted(user.loyalty_discount), kind="vip")
        return

    # уведомление о новом уровне накопительной скидки (5/10/20 заказов)
    if paid in (5, 10, 20):
        notify(db, user, messages.loyalty_upgraded(user.loyalty_discount), kind="loyalty")
