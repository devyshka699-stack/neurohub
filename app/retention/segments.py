"""Сегментация клиентов по частоте заказов."""

from dataclasses import dataclass

from .. import config


@dataclass
class Segment:
    code: str
    label: str
    badge: str


NEW = Segment("new", "Новый", "bg-slate-100 text-slate-700")
ACTIVE = Segment("active", "Активный", "bg-blue-100 text-blue-800")
LOYAL = Segment("loyal", "Постоянный", "bg-violet-100 text-violet-800")
VIP = Segment("vip", "VIP", "bg-amber-100 text-amber-800")
CHURNING = Segment("churning", "Уходящий", "bg-red-100 text-red-800")


def segment_of(paid_orders: int, days_since_last: int | None, is_vip: bool) -> Segment:
    """Определяет сегмент по числу оплаченных заказов и давности активности."""
    if is_vip or paid_orders >= config.VIP_ORDERS_THRESHOLD:
        return VIP
    if paid_orders == 0:
        return NEW
    if days_since_last is not None and days_since_last >= config.WINBACK_DAYS:
        return CHURNING
    if paid_orders >= 2:
        return LOYAL
    return ACTIVE
