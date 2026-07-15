"""Очередь AI-задач на asyncio.Queue с одним фоновым воркером."""

import asyncio
import json
import logging
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Awaitable, Callable

from sqlalchemy.orm import Session

from .. import config
from ..database import SessionLocal
from ..models import STATUS_DONE, STATUS_REVIEW, Order
from . import cache
from .handlers import run_handler
from .handlers.base import AIResult
from .tasks import detect_task
from ..qc import run_qc
from ..qc.result import QCResult

log = logging.getLogger("ai.queue")

AI_QUEUED = "queued"
AI_PROCESSING = "processing"
AI_DONE = "done"
AI_ERROR = "error"

_queue: asyncio.Queue[int] = asyncio.Queue()
_worker: asyncio.Task | None = None

# Подписчики на завершение обработки (успех или ошибка), получают order_id.
# Используется Telegram-ботом для отправки результата в чат.
_listeners: list[Callable[[int], Awaitable[None]]] = []


def add_listener(callback: Callable[[int], Awaitable[None]]) -> None:
    _listeners.append(callback)


async def _notify_listeners(order_id: int) -> None:
    for callback in _listeners:
        try:
            await callback(order_id)
        except Exception:
            log.exception("Ошибка слушателя для заказа %s", order_id)


def start() -> None:
    """Запускает воркер и возвращает в очередь зависшие задачи (после рестарта)."""
    global _worker
    if _worker is None or _worker.done():
        _worker = asyncio.get_event_loop().create_task(_worker_loop())

    with SessionLocal() as db:
        stuck = (
            db.query(Order)
            .filter(Order.ai_status.in_([AI_QUEUED, AI_PROCESSING]))
            .all()
        )
        for order in stuck:
            order.ai_status = AI_QUEUED
            _queue.put_nowait(order.id)
        db.commit()


def enqueue(order: Order, db: Session) -> None:
    order.ai_status = AI_QUEUED
    order.ai_error = None
    db.commit()
    _queue.put_nowait(order.id)


async def _worker_loop() -> None:
    log.info("AI-воркер запущен")
    while True:
        order_id = await _queue.get()
        try:
            await _process(order_id)
        except Exception:
            log.exception("Необработанная ошибка при заказе %s", order_id)
        finally:
            _queue.task_done()


async def _process(order_id: int) -> None:
    with SessionLocal() as db:
        order = db.get(Order, order_id)
        if order is None or order.ai_status not in (AI_QUEUED, AI_PROCESSING):
            return
        order.ai_status = AI_PROCESSING
        db.commit()

        service_title = order.service.title
        description = order.description
        input_path = (
            config.UPLOAD_DIR / order.file_path if order.file_path else None
        )
        if input_path is not None and not input_path.exists():
            input_path = None

    deliver = False
    try:
        task = detect_task(service_title)
        file_bytes = input_path.read_bytes() if input_path else None
        key = cache.make_key(task, description, file_bytes)

        qc_result = None
        attempts_made = 0

        cached = cache.get(key)
        if cached is not None:
            # в кэш попадают только прошедшие проверку результаты
            src, filename, comment = cached
            comment = f"{comment} · результат из кэша"
            stored = _store_result(src)
            qc_passed = True
        else:
            with tempfile.TemporaryDirectory(prefix="ai_task_") as tmp:
                chosen, qc_result, attempts_made = await _generate_with_qc(
                    task, description, input_path, Path(tmp)
                )
                filename, comment = chosen.filename, chosen.comment
                stored = _store_result(chosen.path)
                qc_passed = qc_result is None or qc_result.passed
                if qc_passed:
                    cache.put(key, chosen.path, chosen.filename, chosen.comment)

        # решаем финальный статус с учётом контроля качества
        with SessionLocal() as db:
            order = db.get(Order, order_id)
            order.result_path = stored
            order.result_name = filename
            order.result_comment = comment
            order.ai_status = AI_DONE
            order.ai_error = None
            order.qc_attempts = attempts_made
            if qc_result is not None:
                order.qc_status = "passed" if qc_result.passed else "failed"
                order.qc_score = qc_result.score
                order.qc_report = json.dumps(qc_result.to_dict(), ensure_ascii=False)
            else:
                order.qc_status = "passed"

            if not qc_passed:
                # автопроверка провалена после всех попыток — на ручное решение
                order.status = STATUS_REVIEW
                log.info("Заказ %s: QC не пройден (%s баллов), на ручную проверку",
                         order_id, qc_result.score if qc_result else "?")
            elif config.QC_REQUIRE_APPROVAL:
                # автопроверка пройдена — ждём подтверждения человеком
                order.status = STATUS_REVIEW
                log.info("Заказ %s выполнен AI (%s), ждёт подтверждения", order_id, task)
            else:
                order.status = STATUS_DONE
                deliver = True
                log.info("Заказ %s выполнен AI (%s), отправлен клиенту", order_id, task)
            db.commit()

        if deliver:
            await _deliver(order_id)

        # заказ ушёл в статус «на подтверждении» — уведомляем администратора
        if not deliver:
            await _notify_admin_review(order_id)

    except Exception as exc:
        log.exception("Ошибка AI-обработки заказа %s", order_id)
        with SessionLocal() as db:
            order = db.get(Order, order_id)
            order.ai_status = AI_ERROR
            order.ai_error = str(exc)
            db.commit()
        # сообщаем клиенту, что заказ уйдёт оператору (для Telegram-заказов)
        await _notify_listeners(order_id)


async def _notify_admin_review(order_id: int) -> None:
    """Уведомляет администратора в Telegram, что заказ ждёт подтверждения."""
    try:
        from .. import tgbot
        from ..models import User
        if tgbot.bot is None:
            return
        with SessionLocal() as db:
            order = db.get(Order, order_id)
            if order is None:
                return
            admin = (
                db.query(User)
                .filter(User.is_admin.is_(True), User.telegram_id.isnot(None))
                .first()
            )
            if admin is None:
                return
            qc = f" · QC {order.qc_score}" if order.qc_score is not None else ""
            flag = "⚠️ низкое качество" if order.qc_status == "failed" else "✅ QC пройден"
            text = (
                f"🔔 Заказ №{order_id} готов и ждёт подтверждения ({flag}{qc}).\n"
                f"Проверьте в админ-панели."
            )
            chat_id = admin.telegram_id
        tgbot.schedule_message(chat_id, text)
    except Exception:
        log.debug("Не удалось уведомить администратора о заказе %s", order_id)


async def _generate_with_qc(
    task: str, description: str, input_path: Path | None, tmp: Path
) -> tuple[AIResult, QCResult | None, int]:
    """Генерирует результат и проверяет качество, перегенерируя при неудаче.

    Возвращает (лучший_результат, отчёт_QC_или_None, число_попыток).
    Если QC выключен — одна генерация без проверки.
    """
    if not config.QC_ENABLED:
        adir = tmp / "a0"
        adir.mkdir()
        result = await run_handler(task, description, input_path, adir, attempt=0)
        return result, None, 1

    best: AIResult | None = None
    best_qc: QCResult | None = None
    attempts = 0

    for attempt in range(config.QC_MAX_ATTEMPTS):
        attempts = attempt + 1
        adir = tmp / f"a{attempt}"
        adir.mkdir()
        result = await run_handler(task, description, input_path, adir, attempt=attempt)
        qc = await asyncio.to_thread(run_qc, task, result.path, description)
        log.info(
            "Заказ QC попытка %s/%s: %s баллов (%s)",
            attempts, config.QC_MAX_ATTEMPTS, qc.score,
            "OK" if qc.passed else "низкое качество",
        )
        if best_qc is None or qc.score > best_qc.score:
            best, best_qc = result, qc
        if qc.passed:
            return result, qc, attempts

    # ни одна попытка не прошла порог — возвращаем лучшую
    return best, best_qc, attempts


async def _deliver(order_id: int) -> None:
    """Доставка результата клиенту + хук удержания (после DONE)."""
    try:
        from ..retention import service as retention
        await asyncio.to_thread(retention.on_order_completed, order_id)
    except Exception:
        log.exception("Хук удержания для заказа %s упал", order_id)
    await _notify_listeners(order_id)


def _store_result(src: Path) -> str:
    """Копирует результат в uploads/, возвращает имя файла на диске."""
    stored = f"{uuid.uuid4().hex}{src.suffix}"
    shutil.copy2(src, config.UPLOAD_DIR / stored)
    return stored
