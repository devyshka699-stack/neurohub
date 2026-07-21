"""API для локального воркера на Маке (обработка заказов с Render)."""

import logging
import secrets
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session, joinedload

from .. import config
from ..ai import queue as ai_queue
from ..database import get_db
from ..models import STATUS_DONE, STATUS_IN_PROGRESS, STATUS_REVIEW, Order

log = logging.getLogger("worker_api")
router = APIRouter(prefix="/api/worker", tags=["worker"])


def require_worker(
    authorization: str | None = Header(None),
    x_worker_token: str | None = Header(None),
):
    """Проверка Bearer или X-Worker-Token."""
    if not config.WORKER_TOKEN:
        raise HTTPException(503, "WORKER_TOKEN не задан на сервере")
    token = None
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    elif x_worker_token:
        token = x_worker_token.strip()
    if not token or not secrets.compare_digest(token, config.WORKER_TOKEN):
        raise HTTPException(401, "Неверный WORKER_TOKEN")


def _pending_filter(db: Session) -> list[Order]:
    """Заказы, которые воркер может взять: queued/error/None или зависший processing."""
    stale_before = datetime.utcnow() - timedelta(minutes=config.WORKER_STALE_MINUTES)
    orders = (
        db.query(Order)
        .options(joinedload(Order.service))
        .filter(Order.status == STATUS_IN_PROGRESS)
        .filter(Order.result_path.is_(None))
        .order_by(Order.created_at.asc())
        .all()
    )
    out = []
    for o in orders:
        st = o.ai_status
        if st in (None, ai_queue.AI_QUEUED, ai_queue.AI_ERROR):
            out.append(o)
        elif st == ai_queue.AI_PROCESSING and (o.updated_at or o.created_at) < stale_before:
            out.append(o)
    return out


@router.get("/orders/pending")
def list_pending(
    _: None = Depends(require_worker),
    db: Session = Depends(get_db),
):
    orders = _pending_filter(db)
    return {
        "orders": [
            {
                "id": o.id,
                "service_title": o.service.title if o.service else "",
                "description": o.description,
                "has_file": bool(o.file_path),
                "file_name": o.file_name,
                "ai_status": o.ai_status,
                "source": o.source,
            }
            for o in orders
        ]
    }


@router.post("/orders/{order_id}/claim")
def claim_order(
    order_id: int,
    _: None = Depends(require_worker),
    db: Session = Depends(get_db),
):
    order = db.get(Order, order_id)
    if order is None:
        raise HTTPException(404, "Заказ не найден")
    if order.status != STATUS_IN_PROGRESS:
        raise HTTPException(400, f"Заказ не в работе (status={order.status})")
    if order.result_path:
        raise HTTPException(400, "У заказа уже есть результат")

    stale_before = datetime.utcnow() - timedelta(minutes=config.WORKER_STALE_MINUTES)
    st = order.ai_status
    can_claim = st in (None, ai_queue.AI_QUEUED, ai_queue.AI_ERROR) or (
        st == ai_queue.AI_PROCESSING
        and (order.updated_at or order.created_at) < stale_before
    )
    if not can_claim:
        raise HTTPException(409, f"Заказ уже обрабатывается (ai_status={st})")

    order.ai_status = ai_queue.AI_PROCESSING
    order.ai_error = None
    order.updated_at = datetime.utcnow()
    db.commit()
    return {"ok": True, "id": order.id, "ai_status": order.ai_status}


@router.get("/orders/{order_id}/file")
def download_input_file(
    order_id: int,
    _: None = Depends(require_worker),
    db: Session = Depends(get_db),
):
    order = db.get(Order, order_id)
    if order is None or not order.file_path:
        raise HTTPException(404, "Файл не найден")
    path = config.UPLOAD_DIR / order.file_path
    if not path.exists():
        raise HTTPException(404, "Файл отсутствует на диске")
    return FileResponse(
        path,
        filename=order.file_name or path.name,
        media_type="application/octet-stream",
    )


@router.post("/orders/{order_id}/result")
async def upload_result(
    order_id: int,
    _: None = Depends(require_worker),
    db: Session = Depends(get_db),
    result_file: UploadFile = File(...),
    result_name: str | None = Form(None),
    result_comment: str = Form(""),
    qc_status: str | None = Form(None),
    qc_score: int | None = Form(None),
    qc_report: str | None = Form(None),
    qc_attempts: int = Form(1),
):
    order = db.get(Order, order_id)
    if order is None:
        raise HTTPException(404)
    if order.status != STATUS_IN_PROGRESS:
        raise HTTPException(400, "Заказ не в работе")

    suffix = Path(result_file.filename or "result.bin").suffix[:16]
    stored = f"{uuid.uuid4().hex}{suffix}"
    dest = config.UPLOAD_DIR / stored
    size = 0
    with dest.open("wb") as out:
        while chunk := await result_file.read(1024 * 1024):
            size += len(chunk)
            if size > config.MAX_UPLOAD_SIZE * 4:
                out.close()
                dest.unlink(missing_ok=True)
                raise HTTPException(413, "Файл результата слишком большой")
            out.write(chunk)

    order.result_path = stored
    order.result_name = result_name or result_file.filename or stored
    order.result_comment = result_comment or "Обработано удалённым воркером (Мак)"
    order.ai_status = ai_queue.AI_DONE
    order.ai_error = None
    order.qc_attempts = qc_attempts
    if qc_status:
        order.qc_status = qc_status
    else:
        order.qc_status = "passed"
    if qc_score is not None:
        order.qc_score = qc_score
    if qc_report:
        order.qc_report = qc_report

    qc_passed = order.qc_status != "failed"
    deliver = False
    if not qc_passed:
        order.status = STATUS_REVIEW
    elif config.QC_REQUIRE_APPROVAL:
        order.status = STATUS_REVIEW
    else:
        order.status = STATUS_DONE
        deliver = True
    order.updated_at = datetime.utcnow()
    db.commit()

    if deliver:
        if order.source == "telegram":
            from .. import tgbot
            tgbot.schedule_notify(order.id)
        from ..retention import service as retention
        retention.on_order_completed(order.id)
    else:
        try:
            from ..ai import queue as q
            import asyncio
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(q._notify_admin_review(order.id))
            except RuntimeError:
                pass
        except Exception:
            log.debug("Не удалось уведомить админа о review %s", order_id)

    return {
        "ok": True,
        "id": order.id,
        "status": order.status,
        "ai_status": order.ai_status,
        "qc_status": order.qc_status,
    }


@router.post("/orders/{order_id}/error")
def report_error(
    order_id: int,
    _: None = Depends(require_worker),
    db: Session = Depends(get_db),
    error: str = Form(...),
):
    order = db.get(Order, order_id)
    if order is None:
        raise HTTPException(404)
    order.ai_status = ai_queue.AI_ERROR
    order.ai_error = error[:4000]
    order.updated_at = datetime.utcnow()
    db.commit()
    if order.source == "telegram":
        try:
            from .. import tgbot
            tgbot.schedule_notify(order.id)
        except Exception:
            pass
    return {"ok": True, "id": order.id, "ai_status": order.ai_status}
