"""Модуль 7: финансовая статистика для дашборда.

Все суммы считаются по оплаченным заказам (оплата подтверждена):
статусы in_progress / review / done. Используется цена со скидкой.
Результаты кэшируются на FINANCE_CACHE_SECONDS (по умолчанию час).
"""

import calendar
from datetime import datetime, timedelta

from sqlalchemy.orm import Session, joinedload

from . import config
from .models import (
    STATUS_DONE,
    STATUS_IN_PROGRESS,
    STATUS_REVIEW,
    Order,
)

PAID_STATUSES = (STATUS_IN_PROGRESS, STATUS_REVIEW, STATUS_DONE)

_cache: dict = {"at": None, "data": None}


def get_stats(db: Session, force: bool = False) -> dict:
    """Возвращает статистику из кэша или пересчитывает раз в час."""
    now = datetime.utcnow()
    if (
        not force
        and _cache["data"] is not None
        and _cache["at"] is not None
        and (now - _cache["at"]).total_seconds() < config.FINANCE_CACHE_SECONDS
    ):
        return _cache["data"]
    data = compute_stats(db)
    _cache["at"] = now
    _cache["data"] = data
    return data


def _price(order: Order) -> int:
    if order.final_price is not None:
        return order.final_price
    return order.service.price if order.service else 0


def _channel(order: Order) -> str:
    if order.user is not None and order.user.referrer_id is not None:
        return "Реферальная программа"
    if order.source == "telegram":
        return "Telegram-бот"
    return "Сайт"


def compute_stats(db: Session) -> dict:
    now = datetime.utcnow()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = today_start - timedelta(days=now.weekday())  # с понедельника
    month_start = today_start.replace(day=1)

    paid: list[Order] = (
        db.query(Order)
        .options(joinedload(Order.service), joinedload(Order.user))
        .filter(Order.status.in_(PAID_STATUSES))
        .all()
    )
    all_orders_count = db.query(Order).count()

    def period(orders, since):
        sel = [o for o in orders if o.created_at >= since]
        return {"revenue": sum(_price(o) for o in sel), "orders": len(sel)}

    today = period(paid, today_start)
    week = period(paid, week_start)
    month = period(paid, month_start)
    total_revenue = sum(_price(o) for o in paid)

    avg_check = round(total_revenue / len(paid)) if paid else 0
    month_avg_check = (
        round(month["revenue"] / month["orders"]) if month["orders"] else 0
    )

    # самая популярная услуга (по числу оплаченных заказов)
    by_service: dict[str, dict] = {}
    for o in paid:
        title = o.service.title if o.service else "—"
        icon = o.service.icon if o.service else ""
        s = by_service.setdefault(title, {"icon": icon, "orders": 0, "revenue": 0})
        s["orders"] += 1
        s["revenue"] += _price(o)
    services_rank = sorted(by_service.items(), key=lambda kv: -kv[1]["orders"])
    top_service = (
        {"title": services_rank[0][0], **services_rank[0][1]}
        if services_rank else None
    )

    # самый прибыльный канал привлечения
    by_channel: dict[str, dict] = {}
    for o in paid:
        ch = by_channel.setdefault(_channel(o), {"orders": 0, "revenue": 0})
        ch["orders"] += 1
        ch["revenue"] += _price(o)
    channels_rank = sorted(by_channel.items(), key=lambda kv: -kv[1]["revenue"])
    top_channel = (
        {"name": channels_rank[0][0], **channels_rank[0][1]}
        if channels_rank else None
    )

    # ряды для графика роста: выручка по дням за последние 30 дней + накопительно
    days = 30
    labels, daily, cumulative = [], [], []
    running = 0
    for i in range(days - 1, -1, -1):
        d0 = today_start - timedelta(days=i)
        d1 = d0 + timedelta(days=1)
        rev = sum(_price(o) for o in paid if d0 <= o.created_at < d1)
        labels.append(d0.strftime("%d.%m"))
        daily.append(rev)
        running += rev
        cumulative.append(running)

    # прогноз на месяц: текущий темп (run-rate) по прошедшим дням месяца
    days_in_month = calendar.monthrange(now.year, now.month)[1]
    day_of_month = now.day
    forecast = (
        round(month["revenue"] / day_of_month * days_in_month)
        if month["revenue"] else 0
    )

    return {
        "generated_at": now,
        "today": today,
        "week": week,
        "month": month,
        "total_revenue": total_revenue,
        "paid_orders": len(paid),
        "all_orders": all_orders_count,
        "avg_check": avg_check,
        "month_avg_check": month_avg_check,
        "top_service": top_service,
        "services": [
            {"title": t, **v} for t, v in services_rank
        ],
        "top_channel": top_channel,
        "channels": [
            {"name": n, **v} for n, v in channels_rank
        ],
        "chart_labels": labels,
        "chart_daily": daily,
        "chart_cumulative": cumulative,
        "forecast": forecast,
        "days_in_month": days_in_month,
        "day_of_month": day_of_month,
    }
