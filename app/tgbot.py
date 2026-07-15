"""Telegram-бот для приёма заказов (aiogram 3.x).

Работает в одном процессе с FastAPI: общая SQLite-база и общая AI-очередь.
Оплата — Telegram Stars (XTR) или реферальный баланс.
"""

import asyncio
import logging
import math
import secrets
import uuid
from pathlib import Path

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import CommandStart, Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    BotCommand,
    CallbackQuery,
    ChosenInlineResult,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQuery,
    InlineQueryResultArticle,
    InlineQueryResultsButton,
    InputTextMessageContent,
    LabeledPrice,
    Message,
    PreCheckoutQuery,
)

from . import config
from .ai import queue as ai_queue
from .ai.tasks import detect_task
from .database import SessionLocal
from .models import STATUS_DONE, STATUS_IN_PROGRESS, STATUS_NEW, Order, Service, User
from .security import hash_password

log = logging.getLogger("tgbot")
router = Router()

bot: Bot | None = None
_loop: asyncio.AbstractEventLoop | None = None
_bot_username: str = ""

# описание из inline-режима, ожидающее оформления: telegram_id -> (task, query)
_pending_inline: dict[int, tuple[str, str]] = {}


class OrderFlow(StatesGroup):
    description = State()
    file = State()


# ---------- запуск ----------

def start() -> None:
    """Запускает поллинг бота фоновой задачей внутри процесса FastAPI."""
    global bot, _loop
    if not config.TELEGRAM_BOT_TOKEN:
        log.info("TELEGRAM_BOT_TOKEN не задан — Telegram-бот не запущен")
        return
    _loop = asyncio.get_event_loop()
    bot = Bot(config.TELEGRAM_BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    ai_queue.add_listener(notify_order)
    _loop.create_task(_run(dp))


async def _run(dp: Dispatcher) -> None:
    global _bot_username
    me = await bot.get_me()
    _bot_username = me.username
    await bot.set_my_commands([
        BotCommand(command="start", description="Каталог услуг"),
        BotCommand(command="orders", description="Мои заказы"),
        BotCommand(command="balance", description="Баланс"),
        BotCommand(command="ref", description="Реферальная ссылка"),
    ])
    await bot.delete_webhook(drop_pending_updates=True)
    log.info("Telegram-бот @%s запущен", _bot_username)
    await dp.start_polling(bot, handle_signals=False)


# ---------- пользователи ----------

def _get_or_create_user(tg_user, referrer_tg_arg: str | None = None) -> User:
    with SessionLocal() as db:
        user = db.query(User).filter(User.telegram_id == tg_user.id).first()
        if user is not None:
            return user
        user = User(
            email=f"tg{tg_user.id}@telegram.local",
            name=tg_user.full_name or f"tg{tg_user.id}",
            password_hash=hash_password(secrets.token_hex(16)),
            telegram_id=tg_user.id,
        )
        # реферальная ссылка: /start ref_<user_id>
        if referrer_tg_arg and referrer_tg_arg.startswith("ref_"):
            try:
                referrer_id = int(referrer_tg_arg[4:])
            except ValueError:
                referrer_id = 0
            referrer = db.get(User, referrer_id)
            if referrer is not None and referrer.telegram_id != tg_user.id:
                user.referrer_id = referrer.id
        db.add(user)
        db.commit()
        return user


def _stars_price(rub: int) -> int:
    return max(1, math.ceil(rub / config.TG_STARS_RATE_RUB))


# ---------- /start и каталог ----------

@router.message(CommandStart())
async def cmd_start(message: Message, command: CommandObject, state: FSMContext):
    await state.clear()
    user = _get_or_create_user(message.from_user, command.args)

    # переход из inline-режима: оформляем заказ с уже введённым описанием
    if command.args == "inline":
        pending = _pending_inline.pop(message.from_user.id, None)
        if pending is not None:
            task, query = pending
            service = _find_service_by_task(task)
            if service is not None:
                await _create_order_and_offer_payment(
                    message, user, service, query, None, None
                )
                return

    await message.answer(
        "👋 Привет! Я выполняю задачи с помощью нейросетей.\n\n"
        "Выберите услугу из каталога:",
        reply_markup=_catalog_keyboard(),
    )


def _catalog_keyboard() -> InlineKeyboardMarkup:
    with SessionLocal() as db:
        services = db.query(Service).filter(Service.is_active.is_(True)).all()
    rows = [
        [InlineKeyboardButton(
            text=f"{s.icon} {s.title} — {s.price}₽ (~{_stars_price(s.price)}⭐)",
            callback_data=f"svc:{s.id}",
        )]
        for s in services
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _find_service_by_task(task: str) -> Service | None:
    with SessionLocal() as db:
        for service in db.query(Service).filter(Service.is_active.is_(True)).all():
            try:
                if detect_task(service.title) == task:
                    return service
            except RuntimeError:
                continue
    return None


# ---------- оформление заказа (FSM) ----------

@router.callback_query(F.data.startswith("svc:"))
async def choose_service(callback: CallbackQuery, state: FSMContext):
    service_id = int(callback.data.split(":")[1])
    with SessionLocal() as db:
        service = db.get(Service, service_id)
    if service is None:
        await callback.answer("Услуга не найдена", show_alert=True)
        return
    await state.set_state(OrderFlow.description)
    await state.update_data(service_id=service_id)
    await callback.message.answer(
        f"{service.icon} <b>{service.title}</b> — {service.price}₽\n\n"
        "📝 Опишите задачу максимально подробно одним сообщением:",
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(OrderFlow.description, F.text)
async def got_description(message: Message, state: FSMContext):
    await state.update_data(description=message.text.strip())
    await state.set_state(OrderFlow.file)
    await message.answer(
        "📎 Если нужен файл (например, фото для обработки) — пришлите его сейчас.\n"
        "Если файл не нужен, нажмите «Пропустить».",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Пропустить ➡️", callback_data="skip_file")]
        ]),
    )


@router.callback_query(OrderFlow.file, F.data == "skip_file")
async def skip_file(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await _finish_order(callback.message, callback.from_user, state, None, None)


@router.message(OrderFlow.file, F.photo)
async def got_photo(message: Message, state: FSMContext):
    photo = message.photo[-1]
    stored = f"{uuid.uuid4().hex}.jpg"
    await bot.download(photo, destination=config.UPLOAD_DIR / stored)
    await _finish_order(message, message.from_user, state, stored, "photo.jpg")


@router.message(OrderFlow.file, F.document)
async def got_document(message: Message, state: FSMContext):
    doc = message.document
    if doc.file_size and doc.file_size > config.MAX_UPLOAD_SIZE:
        await message.answer("Файл слишком большой (максимум 20 МБ). Пришлите другой.")
        return
    suffix = Path(doc.file_name or "file.bin").suffix[:16]
    stored = f"{uuid.uuid4().hex}{suffix}"
    await bot.download(doc, destination=config.UPLOAD_DIR / stored)
    await _finish_order(message, message.from_user, state, stored, doc.file_name)


async def _finish_order(
    message: Message, tg_user, state: FSMContext,
    file_path: str | None, file_name: str | None,
):
    data = await state.get_data()
    await state.clear()
    user = _get_or_create_user(tg_user)
    with SessionLocal() as db:
        service = db.get(Service, data["service_id"])
    await _create_order_and_offer_payment(
        message, user, service, data["description"], file_path, file_name
    )


async def _create_order_and_offer_payment(
    message: Message, user: User, service: Service,
    description: str, file_path: str | None, file_name: str | None,
):
    contact = f"@{message.chat.username}" if message.chat.username else f"tg:{message.chat.id}"
    from .retention import service as retention
    with SessionLocal() as db:
        db_user = db.get(User, user.id)
        base_price, discount, final_price, _promo, _err = (
            retention.compute_order_pricing(db, db_user, service, None)
        )
        order = Order(
            user_id=user.id,
            service_id=service.id,
            description=description,
            contact=contact,
            payment_method="stars",
            source="telegram",
            tg_chat_id=message.chat.id,
            file_path=file_path,
            file_name=file_name,
            base_price=base_price,
            discount_percent=discount,
            final_price=final_price,
        )
        db.add(order)
        db.commit()
        order_id = order.id
        price_to_pay = order.price_to_pay

    stars = _stars_price(price_to_pay)
    buttons = [[InlineKeyboardButton(
        text=f"⭐ Оплатить {stars} Stars", callback_data=f"pay_stars:{order_id}"
    )]]
    if user.balance >= price_to_pay:
        buttons.append([InlineKeyboardButton(
            text=f"💰 Оплатить с баланса ({user.balance}₽)",
            callback_data=f"pay_balance:{order_id}",
        )])
    discount_note = f" (скидка {discount}%)" if discount else ""
    await message.answer(
        f"✅ Заказ №{order_id} создан!\n\n"
        f"{service.icon} {service.title}\n"
        f"💵 К оплате: {price_to_pay}₽{discount_note} = {stars}⭐",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )


# ---------- оплата ----------

@router.callback_query(F.data.startswith("pay_stars:"))
async def pay_stars(callback: CallbackQuery):
    order_id = int(callback.data.split(":")[1])
    with SessionLocal() as db:
        order = db.get(Order, order_id)
        if order is None or order.status != STATUS_NEW:
            await callback.answer("Заказ не найден или уже оплачен", show_alert=True)
            return
        title = order.service.title
        stars = _stars_price(order.price_to_pay)
    await callback.answer()
    await bot.send_invoice(
        chat_id=callback.message.chat.id,
        title=title[:32],
        description=f"Заказ №{order_id}: {title}",
        payload=f"order:{order_id}",
        currency="XTR",
        prices=[LabeledPrice(label=title[:32], amount=stars)],
    )


@router.pre_checkout_query()
async def pre_checkout(query: PreCheckoutQuery):
    order_id = int(query.invoice_payload.split(":")[1])
    with SessionLocal() as db:
        order = db.get(Order, order_id)
        ok = order is not None and order.status == STATUS_NEW
    await query.answer(ok=ok, error_message="Заказ не найден или уже оплачен")


@router.message(F.successful_payment)
async def successful_payment(message: Message):
    order_id = int(message.successful_payment.invoice_payload.split(":")[1])
    await _mark_paid(message, order_id, method="stars")


@router.callback_query(F.data.startswith("pay_balance:"))
async def pay_balance(callback: CallbackQuery):
    order_id = int(callback.data.split(":")[1])
    with SessionLocal() as db:
        order = db.get(Order, order_id)
        if order is None or order.status != STATUS_NEW:
            await callback.answer("Заказ не найден или уже оплачен", show_alert=True)
            return
        user = order.user
        price = order.price_to_pay
        if user.balance < price:
            await callback.answer("Недостаточно средств на балансе", show_alert=True)
            return
        user.balance -= price
        order.payment_method = "balance"
        db.commit()
    await callback.answer()
    await _mark_paid(callback.message, order_id, method="balance")


async def _mark_paid(message: Message, order_id: int, method: str) -> None:
    """Помечает заказ оплаченным, начисляет рефбонус, ставит в AI-очередь."""
    referrer_chat: int | None = None
    with SessionLocal() as db:
        order = db.get(Order, order_id)
        if order is None or order.status != STATUS_NEW:
            return
        order.status = STATUS_IN_PROGRESS
        user = order.user

        # реферальный бонус — после первой оплаты приведённого друга
        if user.referrer_id and not user.referral_rewarded:
            referrer = db.get(User, user.referrer_id)
            if referrer is not None:
                referrer.balance += config.REFERRAL_BONUS
                user.referral_rewarded = True
                referrer_chat = referrer.telegram_id
        db.commit()

        if config.AI_AUTO_PROCESS:
            ai_queue.enqueue(order, db)

    await message.answer(
        f"💳 Оплата получена! Заказ №{order_id} взят в работу.\n"
        "Результат придёт прямо в этот чат. Статус: /orders"
    )
    if referrer_chat:
        try:
            await bot.send_message(
                referrer_chat,
                f"🎉 Ваш друг оплатил первый заказ — вам начислено "
                f"{config.REFERRAL_BONUS}₽ на баланс! Проверить: /balance",
            )
        except Exception:
            log.warning("Не удалось уведомить реферера %s", referrer_chat)


# ---------- доставка результата в чат ----------

async def notify_order(order_id: int) -> None:
    """Вызывается AI-очередью (или админкой) после завершения заказа."""
    if bot is None:
        return
    with SessionLocal() as db:
        order = db.get(Order, order_id)
        if order is None or order.source != "telegram" or not order.tg_chat_id:
            return
        chat_id = order.tg_chat_id
        status = order.status
        ai_status = order.ai_status
        comment = order.result_comment or ""
        result_path = (
            config.UPLOAD_DIR / order.result_path if order.result_path else None
        )
        result_name = order.result_name or "result"

    if status == STATUS_DONE and result_path is not None and result_path.exists():
        caption = f"🎉 Заказ №{order_id} выполнен!\n{comment}"[:1024]
        file = FSInputFile(result_path, filename=result_name)
        suffix = result_path.suffix.lower()
        try:
            if suffix in (".png", ".jpg", ".jpeg", ".webp"):
                await bot.send_photo(chat_id, file, caption=caption)
                await bot.send_document(
                    chat_id, file, caption="Оригинал файла без сжатия"
                )
            elif suffix in (".mp3", ".wav", ".ogg"):
                await bot.send_audio(chat_id, file, caption=caption)
            elif suffix in (".txt", ".md"):
                text = result_path.read_text(errors="ignore")
                await bot.send_message(chat_id, f"{caption}\n\n{text}"[:4000])
                await bot.send_document(chat_id, file)
            else:
                await bot.send_document(chat_id, file, caption=caption)
        except Exception:
            log.exception("Не удалось отправить результат заказа %s", order_id)
    elif ai_status == "error":
        await bot.send_message(
            chat_id,
            f"⏳ Заказ №{order_id}: автоматическая обработка не удалась, "
            "заказ передан оператору и будет выполнен вручную.",
        )


def schedule_notify(order_id: int) -> None:
    """Уведомление из синхронного кода (админка выполняет заказ вручную)."""
    if bot is None or _loop is None:
        return
    asyncio.run_coroutine_threadsafe(notify_order(order_id), _loop)


async def _send_message(chat_id: int, text: str) -> None:
    try:
        await bot.send_message(chat_id, text, parse_mode="HTML")
    except Exception:
        log.warning("Не удалось отправить сообщение в чат %s", chat_id)


def schedule_message(chat_id: int, text: str) -> None:
    """Отправка произвольного сообщения клиенту из синхронного кода (модуль 5)."""
    if bot is None or _loop is None:
        raise RuntimeError("Telegram-бот не запущен")
    asyncio.run_coroutine_threadsafe(_send_message(chat_id, text), _loop)


# ---------- inline-режим ----------

@router.inline_query()
async def inline_query(query: InlineQuery):
    text = query.query.strip()
    if not text:
        await query.answer(
            [], cache_time=5, is_personal=True,
            button=InlineQueryResultsButton(
                text="Введите описание задачи…", start_parameter="start"
            ),
        )
        return

    variants = [
        ("text", "✍️ Сгенерировать текст", "Напишу текст по вашему описанию"),
        ("image", "🎨 Сгенерировать картинку", "Создам изображение по описанию"),
    ]
    results = []
    for task, title, desc in variants:
        service = _find_service_by_task(task)
        price = f" — {service.price}₽" if service else ""
        results.append(InlineQueryResultArticle(
            id=task,
            title=f"{title}{price}",
            description=f"«{text[:80]}»  ·  {desc}",
            input_message_content=InputTextMessageContent(
                message_text=f"Хочу заказать: {title.split(' ', 1)[1]}\n«{text}»"
            ),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(
                    text="🛒 Оформить заказ",
                    url=f"https://t.me/{_bot_username}?start=inline",
                )
            ]]),
        ))
    await query.answer(results, cache_time=0, is_personal=True)


@router.chosen_inline_result()
async def chosen_inline(result: ChosenInlineResult):
    _pending_inline[result.from_user.id] = (result.result_id, result.query.strip())


# ---------- сервисные команды ----------

@router.message(Command("orders"))
async def cmd_orders(message: Message):
    user = _get_or_create_user(message.from_user)
    with SessionLocal() as db:
        orders = (
            db.query(Order)
            .filter(Order.user_id == user.id)
            .order_by(Order.created_at.desc())
            .limit(15)
            .all()
        )
        if not orders:
            await message.answer("У вас пока нет заказов. Каталог: /start")
            return
        lines = [
            f"№{o.id} · {o.service.icon} {o.service.title} · {o.status_label}"
            for o in orders
        ]
    await message.answer("📦 Ваши заказы:\n\n" + "\n".join(lines))


@router.message(Command("balance"))
async def cmd_balance(message: Message):
    user = _get_or_create_user(message.from_user)
    await message.answer(
        f"💰 Ваш баланс: {user.balance}₽\n\n"
        f"Баланс пополняется за приглашённых друзей ({config.REFERRAL_BONUS}₽ "
        "за друга) и им можно оплачивать заказы. Ссылка для друзей: /ref"
    )


@router.message(Command("ref"))
async def cmd_ref(message: Message):
    user = _get_or_create_user(message.from_user)
    link = f"https://t.me/{_bot_username}?start=ref_{user.id}"
    await message.answer(
        "🤝 Приведи друга — получи "
        f"{config.REFERRAL_BONUS}₽ на счёт!\n\n"
        f"Ваша ссылка:\n{link}\n\n"
        "Бонус начисляется после первой оплаты друга. "
        "Балансом можно оплачивать заказы целиком."
    )
