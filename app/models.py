from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base

# Статусы заказа
STATUS_NEW = "new"                  # создан, ждёт оплаты
STATUS_PAYMENT_CHECK = "payment_check"  # клиент сообщил об оплате, ждёт подтверждения
STATUS_IN_PROGRESS = "in_progress"  # оплата подтверждена, в работе
STATUS_REVIEW = "review"            # автопроверка пройдена, ждёт подтверждения человеком
STATUS_DONE = "done"                # выполнен, результат отправлен клиенту
STATUS_CANCELLED = "cancelled"

STATUS_LABELS = {
    STATUS_NEW: "Ожидает оплаты",
    STATUS_PAYMENT_CHECK: "Проверка оплаты",
    STATUS_IN_PROGRESS: "В работе",
    STATUS_REVIEW: "На подтверждении",
    STATUS_DONE: "Выполнен",
    STATUS_CANCELLED: "Отменён",
}

STATUS_BADGES = {
    STATUS_NEW: "bg-amber-100 text-amber-800",
    STATUS_PAYMENT_CHECK: "bg-blue-100 text-blue-800",
    STATUS_IN_PROGRESS: "bg-violet-100 text-violet-800",
    STATUS_REVIEW: "bg-orange-100 text-orange-800",
    STATUS_DONE: "bg-emerald-100 text-emerald-800",
    STATUS_CANCELLED: "bg-gray-200 text-gray-600",
}


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    password_hash: Mapped[str] = mapped_column(String(255))
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # Telegram (модуль 3)
    telegram_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)
    balance: Mapped[int] = mapped_column(Integer, default=0)  # рубли (реферальные бонусы)
    referrer_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    referral_rewarded: Mapped[bool] = mapped_column(Boolean, default=False)

    # Лояльность / удержание (модуль 5)
    is_vip: Mapped[bool] = mapped_column(Boolean, default=False)  # персональный менеджер
    vip_since: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_winback_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_newsletter_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    orders: Mapped[list["Order"]] = relationship(back_populates="user")

    @property
    def paid_orders_count(self) -> int:
        """Количество оплаченных заказов (в работе, выполнены)."""
        return sum(
            1 for o in self.orders
            if o.status in (STATUS_IN_PROGRESS, STATUS_DONE)
        )

    @property
    def loyalty_discount(self) -> int:
        """Накопительная скидка в % по числу оплаченных заказов.

        5 заказов → 10%, 10 → 20%, 20 → 30%. VIP гарантирует пол 20%.
        """
        n = self.paid_orders_count
        tier = 0
        if n >= 20:
            tier = 30
        elif n >= 10:
            tier = 20
        elif n >= 5:
            tier = 10
        if self.is_vip:
            tier = max(tier, 20)  # персональная скидка VIP «навсегда»
        return tier

    @property
    def loyalty_label(self) -> str:
        d = self.loyalty_discount
        if self.is_vip:
            return f"VIP · скидка {d}%"
        if d:
            return f"Постоянный клиент · скидка {d}%"
        return "Новый клиент"


class Service(Base):
    __tablename__ = "services"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text)
    price: Mapped[int] = mapped_column(Integer)  # в рублях
    icon: Mapped[str] = mapped_column(String(16), default="✨")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    orders: Mapped[list["Order"]] = relationship(back_populates="service")


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    service_id: Mapped[int] = mapped_column(ForeignKey("services.id"))

    description: Mapped[str] = mapped_column(Text)
    contact: Mapped[str] = mapped_column(String(255))
    payment_method: Mapped[str] = mapped_column(String(32), default="card")  # card | crypto | stars | balance
    source: Mapped[str] = mapped_column(String(16), default="web")  # web | telegram
    tg_chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    # файл, приложенный клиентом
    file_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    file_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # результат от исполнителя
    result_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    result_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    result_comment: Mapped[str | None] = mapped_column(Text, nullable=True)

    status: Mapped[str] = mapped_column(String(32), default=STATUS_NEW, index=True)

    # Скидка (модуль 5)
    base_price: Mapped[int | None] = mapped_column(Integer, nullable=True)  # цена услуги без скидки
    discount_percent: Mapped[int] = mapped_column(Integer, default=0)
    final_price: Mapped[int | None] = mapped_column(Integer, nullable=True)  # к оплате со скидкой
    promo_code: Mapped[str | None] = mapped_column(String(32), nullable=True)

    # AI-обработка (модуль 2): None | queued | processing | done | error
    ai_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    ai_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Контроль качества (модуль 6): None | passed | failed
    qc_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    qc_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    qc_report: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON-отчёт
    qc_attempts: Mapped[int] = mapped_column(Integer, default=0)
    approved_by_human: Mapped[bool] = mapped_column(Boolean, default=False)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    user: Mapped["User"] = relationship(back_populates="orders")
    service: Mapped["Service"] = relationship(back_populates="orders")

    @property
    def status_label(self) -> str:
        return STATUS_LABELS.get(self.status, self.status)

    @property
    def status_badge(self) -> str:
        return STATUS_BADGES.get(self.status, "bg-gray-200 text-gray-600")

    @property
    def payment_method_label(self) -> str:
        return {
            "card": "карта",
            "crypto": "крипто",
            "stars": "Telegram Stars",
            "balance": "баланс",
        }.get(self.payment_method, self.payment_method)

    @property
    def price_to_pay(self) -> int:
        """Итоговая цена с учётом скидки (или цена услуги, если скидки нет)."""
        if self.final_price is not None:
            return self.final_price
        return self.service.price if self.service else 0

    @property
    def qc_badge(self) -> str:
        return {
            "passed": "bg-emerald-100 text-emerald-800",
            "failed": "bg-red-100 text-red-800",
        }.get(self.qc_status or "", "bg-gray-200 text-gray-600")

    @property
    def qc_report_data(self) -> dict:
        import json
        if not self.qc_report:
            return {}
        try:
            return json.loads(self.qc_report)
        except (ValueError, TypeError):
            return {}

    @property
    def ai_status_label(self) -> str:
        return AI_STATUS_LABELS.get(self.ai_status or "", "")

    @property
    def ai_status_badge(self) -> str:
        return AI_STATUS_BADGES.get(self.ai_status or "", "bg-gray-200 text-gray-600")


AI_STATUS_LABELS = {
    "queued": "🤖 в очереди",
    "processing": "🤖 обрабатывается",
    "done": "🤖 выполнено AI",
    "error": "🤖 ошибка AI",
}

AI_STATUS_BADGES = {
    "queued": "bg-slate-200 text-slate-700",
    "processing": "bg-blue-100 text-blue-800 animate-pulse",
    "done": "bg-emerald-100 text-emerald-800",
    "error": "bg-red-100 text-red-800",
}


# Статусы лида (модуль 4)
LEAD_NEW = "new"            # найден, отклик ещё не сформирован
LEAD_REPLY_READY = "ready"  # отклик сформирован, ждёт отправки
LEAD_REPLIED = "replied"    # отклик отправлен
LEAD_SKIPPED = "skipped"    # пропущен (антиспам/дубль/фильтр)
LEAD_FAILED = "failed"      # ошибка отправки

LEAD_STATUS_LABELS = {
    LEAD_NEW: "Найден",
    LEAD_REPLY_READY: "Отклик готов",
    LEAD_REPLIED: "Отклик отправлен",
    LEAD_SKIPPED: "Пропущен",
    LEAD_FAILED: "Ошибка",
}

LEAD_STATUS_BADGES = {
    LEAD_NEW: "bg-amber-100 text-amber-800",
    LEAD_REPLY_READY: "bg-blue-100 text-blue-800",
    LEAD_REPLIED: "bg-emerald-100 text-emerald-800",
    LEAD_SKIPPED: "bg-gray-200 text-gray-600",
    LEAD_FAILED: "bg-red-100 text-red-800",
}


class Lead(Base):
    """Найденный на внешней площадке потенциальный заказ."""

    __tablename__ = "leads"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(32), index=True)  # kwork, youdo, avito...
    external_id: Mapped[str] = mapped_column(String(255), index=True)  # id/url на площадке
    url: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    title: Mapped[str] = mapped_column(Text)
    snippet: Mapped[str | None] = mapped_column(Text, nullable=True)
    matched_task: Mapped[str | None] = mapped_column(String(32), nullable=True)  # text/image/...
    matched_keyword: Mapped[str | None] = mapped_column(String(255), nullable=True)

    reply_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default=LEAD_NEW, index=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    dry_run: Mapped[bool] = mapped_column(Boolean, default=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    replied_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    __table_args__ = (
        UniqueConstraint("source", "external_id", name="uq_lead_source_external"),
    )

    @property
    def status_label(self) -> str:
        return LEAD_STATUS_LABELS.get(self.status, self.status)

    @property
    def status_badge(self) -> str:
        return LEAD_STATUS_BADGES.get(self.status, "bg-gray-200 text-gray-600")


# ---------- Client retention (модуль 5) ----------

# Поводы выдачи промокода
PROMO_POST_ORDER = "post_order"     # скидка 30% после выполненного заказа
PROMO_WINBACK = "winback"           # возврат неактивного клиента
PROMO_MANUAL = "manual"


class PromoCode(Base):
    __tablename__ = "promo_codes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    discount_percent: Mapped[int] = mapped_column(Integer, default=30)
    reason: Mapped[str] = mapped_column(String(32), default=PROMO_MANUAL)

    used: Mapped[bool] = mapped_column(Boolean, default=False)
    used_order_id: Mapped[int | None] = mapped_column(ForeignKey("orders.id"), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    user: Mapped["User"] = relationship()

    def is_valid(self, now: datetime | None = None) -> bool:
        now = now or datetime.utcnow()
        if self.used:
            return False
        if self.expires_at is not None and now > self.expires_at:
            return False
        return True

    @property
    def status_label(self) -> str:
        if self.used:
            return "Использован"
        if self.expires_at and datetime.utcnow() > self.expires_at:
            return "Истёк"
        return "Активен"


class Notification(Base):
    """Сообщение клиенту (доставляется в Telegram и/или показывается в кабинете)."""

    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    kind: Mapped[str] = mapped_column(String(32), default="info")
    text: Mapped[str] = mapped_column(Text)

    read: Mapped[bool] = mapped_column(Boolean, default=False)
    delivered_tg: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    user: Mapped["User"] = relationship()
