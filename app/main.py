import uuid
from pathlib import Path

from fastapi import Depends, FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware

from . import config, models
from .database import Base, SessionLocal, engine, get_db
from .models import (
    LEAD_STATUS_LABELS,
    STATUS_CANCELLED,
    STATUS_DONE,
    STATUS_IN_PROGRESS,
    STATUS_LABELS,
    STATUS_NEW,
    STATUS_PAYMENT_CHECK,
    STATUS_REVIEW,
    Lead,
    Notification,
    Order,
    PromoCode,
    Service,
    User,
)
from .security import hash_password, verify_password
from .seed import seed
from .ai import queue as ai_queue
from .ai.dispatch import schedule_ai
from .api.worker import router as worker_router
from .ai.tasks import detect_task
from .marketing import seo as seo_pages

Base.metadata.create_all(bind=engine)


def _migrate() -> None:
    """Добавляет недостающие колонки в существующую SQLite-базу."""
    additions = {
        "orders": {
            "ai_status": "VARCHAR(32)",
            "ai_error": "TEXT",
            "source": "VARCHAR(16) DEFAULT 'web'",
            "tg_chat_id": "BIGINT",
            "base_price": "INTEGER",
            "discount_percent": "INTEGER DEFAULT 0",
            "final_price": "INTEGER",
            "promo_code": "VARCHAR(32)",
            "qc_status": "VARCHAR(16)",
            "qc_score": "INTEGER",
            "qc_report": "TEXT",
            "qc_attempts": "INTEGER DEFAULT 0",
            "approved_by_human": "BOOLEAN DEFAULT 0",
        },
        "users": {
            "telegram_id": "BIGINT",
            "balance": "INTEGER DEFAULT 0",
            "referrer_id": "INTEGER",
            "referral_rewarded": "BOOLEAN DEFAULT 0",
            "is_vip": "BOOLEAN DEFAULT 0",
            "vip_since": "DATETIME",
            "last_winback_at": "DATETIME",
            "last_newsletter_at": "DATETIME",
        },
    }
    with engine.begin() as conn:
        for table, columns in additions.items():
            existing = {
                row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info({table})")
            }
            for name, ddl in columns.items():
                if name not in existing:
                    conn.exec_driver_sql(
                        f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"
                    )


_migrate()

with SessionLocal() as _db:
    seed(_db)

app = FastAPI(title=f"{config.SHOP_NAME} — витрина")
app.include_router(worker_router)


@app.on_event("startup")
async def _start_ai_worker():
    # На Render с REMOTE_WORKER локальную AI-очередь всё равно поднимаем
    # (тексты через Groq / rembg), но schedule_ai не ставит туда remote-заказы.
    # Если REMOTE_WORKER — тяжёлые заказы заберёт Мак.
    import logging
    logging.getLogger("app").info(
        "WORKER: token=%s remote=%s",
        "задан" if config.WORKER_TOKEN else "НЕ ЗАДАН",
        "да" if config.REMOTE_WORKER else "нет",
    )
    ai_queue.start()
    from . import tgbot
    await tgbot.start()
    from .marketing import engine as marketing_engine
    marketing_engine.start()
    from .retention import scheduler as retention_scheduler
    retention_scheduler.start()


@app.get("/api/worker/status")
def worker_status():
    """Без токена: видно, подхватил ли сервер WORKER_TOKEN (для отладки Render)."""
    return {
        "worker_token_set": bool(config.WORKER_TOKEN),
        "remote_worker": config.REMOTE_WORKER,
        "token_length": len(config.WORKER_TOKEN),
    }
app.add_middleware(SessionMiddleware, secret_key=config.SECRET_KEY)

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
templates.env.globals["shop_name"] = config.SHOP_NAME
templates.env.globals["support_contact"] = config.SUPPORT_CONTACT
templates.env.globals["crypto_enabled"] = bool(
    config.PAYMENT_CRYPTO_USDT_TRC20 or config.PAYMENT_CRYPTO_BTC
)
static_dir = BASE_DIR / "static"
static_dir.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


# ---------- вспомогательные ----------

def current_user(request: Request, db: Session) -> User | None:
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    return db.get(User, user_id)


def require_user(request: Request, db: Session) -> User:
    user = current_user(request, db)
    if user is None:
        raise HTTPException(status_code=307, headers={"Location": "/login"})
    return user


def require_admin(request: Request, db: Session) -> User:
    user = require_user(request, db)
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Доступ запрещён")
    return user


def render(request: Request, db: Session, template: str, **ctx):
    ctx.setdefault("user", current_user(request, db))
    ctx["request"] = request
    return templates.TemplateResponse(request, template, ctx)


def save_upload(upload: UploadFile | None) -> tuple[str | None, str | None]:
    """Сохраняет файл в uploads/, возвращает (путь_на_диске, исходное_имя)."""
    if upload is None or not upload.filename:
        return None, None
    suffix = Path(upload.filename).suffix[:16]
    stored_name = f"{uuid.uuid4().hex}{suffix}"
    dest = config.UPLOAD_DIR / stored_name
    size = 0
    with dest.open("wb") as out:
        while chunk := upload.file.read(1024 * 1024):
            size += len(chunk)
            if size > config.MAX_UPLOAD_SIZE:
                out.close()
                dest.unlink(missing_ok=True)
                raise HTTPException(413, "Файл слишком большой (максимум 20 МБ)")
            out.write(chunk)
    return stored_name, upload.filename


# ---------- каталог ----------

@app.get("/")
def index(request: Request, db: Session = Depends(get_db)):
    services = db.query(Service).filter(Service.is_active.is_(True)).all()
    return render(request, db, "index.html", services=services)


# ---------- авторизация ----------

@app.get("/register")
def register_page(request: Request, db: Session = Depends(get_db)):
    return render(request, db, "register.html", error=None)


@app.post("/register")
def register(
    request: Request,
    name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    email = email.strip().lower()
    if len(password) < 6:
        return render(request, db, "register.html", error="Пароль должен быть не короче 6 символов")
    if db.query(User).filter(User.email == email).first():
        return render(request, db, "register.html", error="Пользователь с таким email уже существует")
    user = User(email=email, name=name.strip(), password_hash=hash_password(password))
    db.add(user)
    db.commit()
    request.session["user_id"] = user.id
    return RedirectResponse("/cabinet", status_code=303)


@app.get("/login")
def login_page(request: Request, db: Session = Depends(get_db)):
    return render(request, db, "login.html", error=None)


@app.post("/login")
def login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    user = db.query(User).filter(User.email == email.strip().lower()).first()
    if not user or not verify_password(password, user.password_hash):
        return render(request, db, "login.html", error="Неверный email или пароль")
    request.session["user_id"] = user.id
    return RedirectResponse("/admin" if user.is_admin else "/cabinet", status_code=303)


@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/", status_code=303)


# ---------- заказ ----------

@app.get("/order/{service_id}")
def order_page(service_id: int, request: Request, db: Session = Depends(get_db)):
    user = current_user(request, db)
    if user is None:
        return RedirectResponse(f"/login?next=/order/{service_id}", status_code=303)
    service = db.get(Service, service_id)
    if not service or not service.is_active:
        raise HTTPException(404, "Услуга не найдена")
    active_promos = (
        db.query(PromoCode)
        .filter(PromoCode.user_id == user.id, PromoCode.used.is_(False))
        .order_by(PromoCode.created_at.desc())
        .all()
    )
    active_promos = [p for p in active_promos if p.is_valid()]
    return render(
        request, db, "order.html",
        service=service, error=None,
        loyalty_discount=user.loyalty_discount,
        active_promos=active_promos,
    )


@app.post("/order/{service_id}")
def create_order(
    service_id: int,
    request: Request,
    description: str = Form(...),
    contact: str = Form(...),
    payment_method: str = Form("card"),
    promo_code: str = Form(""),
    file: UploadFile | None = None,
    db: Session = Depends(get_db),
):
    user = current_user(request, db)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    service = db.get(Service, service_id)
    if not service:
        raise HTTPException(404, "Услуга не найдена")

    from .retention import service as retention
    base_price, discount, final_price, promo_obj, promo_error = (
        retention.compute_order_pricing(db, user, service, promo_code)
    )
    if promo_error:
        active_promos = [
            p for p in db.query(PromoCode)
            .filter(PromoCode.user_id == user.id, PromoCode.used.is_(False)).all()
            if p.is_valid()
        ]
        return render(
            request, db, "order.html",
            service=service, error=promo_error,
            loyalty_discount=user.loyalty_discount,
            active_promos=active_promos,
        )

    file_path, file_name = save_upload(file)
    order = Order(
        user_id=user.id,
        service_id=service.id,
        description=description.strip(),
        contact=contact.strip(),
        payment_method=payment_method if payment_method in ("card", "crypto") else "card",
        file_path=file_path,
        file_name=file_name,
        base_price=base_price,
        discount_percent=discount,
        final_price=final_price,
        promo_code=promo_obj.code if promo_obj else None,
    )
    db.add(order)
    db.commit()

    # помечаем промокод использованным
    if promo_obj is not None:
        promo_obj.used = True
        promo_obj.used_order_id = order.id
        db.commit()

    return RedirectResponse(f"/order/{order.id}/pay", status_code=303)


@app.get("/order/{order_id}/pay")
def payment_page(order_id: int, request: Request, db: Session = Depends(get_db)):
    user = require_user(request, db)
    order = db.get(Order, order_id)
    if not order or (order.user_id != user.id and not user.is_admin):
        raise HTTPException(404, "Заказ не найден")
    return render(
        request, db, "payment.html",
        order=order,
        card_number=config.PAYMENT_CARD_NUMBER,
        card_holder=config.PAYMENT_CARD_HOLDER,
        card_bank=config.PAYMENT_CARD_BANK,
        usdt_address=config.PAYMENT_CRYPTO_USDT_TRC20,
        btc_address=config.PAYMENT_CRYPTO_BTC,
    )


@app.post("/order/{order_id}/paid")
def mark_paid(order_id: int, request: Request, db: Session = Depends(get_db)):
    user = require_user(request, db)
    order = db.get(Order, order_id)
    if not order or order.user_id != user.id:
        raise HTTPException(404, "Заказ не найден")
    if order.status == STATUS_NEW:
        order.status = STATUS_PAYMENT_CHECK
        db.commit()
    return RedirectResponse("/cabinet", status_code=303)


# ---------- личный кабинет ----------

@app.get("/cabinet")
def cabinet(request: Request, db: Session = Depends(get_db)):
    user = current_user(request, db)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    orders = (
        db.query(Order)
        .filter(Order.user_id == user.id)
        .order_by(Order.created_at.desc())
        .all()
    )
    promos = [
        p for p in db.query(PromoCode)
        .filter(PromoCode.user_id == user.id, PromoCode.used.is_(False))
        .order_by(PromoCode.created_at.desc()).all()
        if p.is_valid()
    ]
    notes = (
        db.query(Notification)
        .filter(Notification.user_id == user.id)
        .order_by(Notification.created_at.desc())
        .limit(20)
        .all()
    )
    # помечаем прочитанными
    unread = [n for n in notes if not n.read]
    for n in unread:
        n.read = True
    if unread:
        db.commit()
    return render(
        request, db, "cabinet.html",
        orders=orders, promos=promos, notes=notes,
    )


@app.get("/order/{order_id}/result")
def download_result(order_id: int, request: Request, db: Session = Depends(get_db)):
    user = require_user(request, db)
    order = db.get(Order, order_id)
    if not order or (order.user_id != user.id and not user.is_admin):
        raise HTTPException(404, "Заказ не найден")
    if not order.result_path:
        raise HTTPException(404, "Результат ещё не готов")
    path = config.UPLOAD_DIR / order.result_path
    if not path.exists():
        raise HTTPException(404, "Файл не найден")
    return FileResponse(path, filename=order.result_name or path.name)


@app.get("/order/{order_id}/file")
def download_client_file(order_id: int, request: Request, db: Session = Depends(get_db)):
    user = require_user(request, db)
    order = db.get(Order, order_id)
    if not order or (order.user_id != user.id and not user.is_admin):
        raise HTTPException(404, "Заказ не найден")
    if not order.file_path:
        raise HTTPException(404, "Файл не прикреплён")
    path = config.UPLOAD_DIR / order.file_path
    if not path.exists():
        raise HTTPException(404, "Файл не найден")
    return FileResponse(path, filename=order.file_name or path.name)


# ---------- админ-панель ----------

@app.get("/admin")
def admin_panel(
    request: Request,
    status: str | None = None,
    db: Session = Depends(get_db),
):
    user = current_user(request, db)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    if not user.is_admin:
        raise HTTPException(403, "Доступ запрещён")
    query = db.query(Order).order_by(Order.created_at.desc())
    if status and status in STATUS_LABELS:
        query = query.filter(Order.status == status)
    orders = query.all()
    counts = {
        s: db.query(Order).filter(Order.status == s).count() for s in STATUS_LABELS
    }
    return render(
        request, db, "admin.html",
        orders=orders,
        counts=counts,
        active_status=status,
        status_labels=STATUS_LABELS,
    )


@app.post("/admin/order/{order_id}/confirm-payment")
def confirm_payment(order_id: int, request: Request, db: Session = Depends(get_db)):
    require_admin(request, db)
    order = db.get(Order, order_id)
    if not order:
        raise HTTPException(404)
    order.status = STATUS_IN_PROGRESS
    db.commit()
    if config.AI_AUTO_PROCESS:
        schedule_ai(order, db)
    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/order/{order_id}/ai")
def process_with_ai(order_id: int, request: Request, db: Session = Depends(get_db)):
    require_admin(request, db)
    order = db.get(Order, order_id)
    if not order:
        raise HTTPException(404)
    if order.ai_status in ("queued", "processing"):
        raise HTTPException(400, "Заказ уже в AI-очереди")
    schedule_ai(order, db)
    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/order/{order_id}/approve")
def approve_order(order_id: int, request: Request, db: Session = Depends(get_db)):
    """Финальное подтверждение человеком: отправить готовый результат клиенту."""
    require_admin(request, db)
    order = db.get(Order, order_id)
    if not order:
        raise HTTPException(404)
    if order.status != STATUS_REVIEW:
        raise HTTPException(400, "Заказ не на подтверждении")
    if not order.result_path:
        raise HTTPException(400, "Нет результата для отправки")
    order.status = STATUS_DONE
    order.approved_by_human = True
    db.commit()
    if order.source == "telegram":
        from . import tgbot
        tgbot.schedule_notify(order.id)
    from .retention import service as retention
    retention.on_order_completed(order.id)
    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/order/{order_id}/redo")
def redo_order(order_id: int, request: Request, db: Session = Depends(get_db)):
    """Перезапустить AI-обработку заказа (например, после проваленного QC)."""
    require_admin(request, db)
    order = db.get(Order, order_id)
    if not order:
        raise HTTPException(404)
    order.qc_status = None
    order.qc_score = None
    order.qc_report = None
    db.commit()
    schedule_ai(order, db)
    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/order/{order_id}/complete")
def complete_order(
    order_id: int,
    request: Request,
    result_comment: str = Form(""),
    result_file: UploadFile | None = None,
    db: Session = Depends(get_db),
):
    require_admin(request, db)
    order = db.get(Order, order_id)
    if not order:
        raise HTTPException(404)
    result_path, result_name = save_upload(result_file)
    if result_path:
        order.result_path = result_path
        order.result_name = result_name
    order.result_comment = result_comment.strip() or None
    order.status = STATUS_DONE
    db.commit()
    if order.source == "telegram":
        from . import tgbot
        tgbot.schedule_notify(order.id)
    from .retention import service as retention
    retention.on_order_completed(order.id)
    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/order/{order_id}/cancel")
def cancel_order(order_id: int, request: Request, db: Session = Depends(get_db)):
    require_admin(request, db)
    order = db.get(Order, order_id)
    if not order:
        raise HTTPException(404)
    order.status = STATUS_CANCELLED
    db.commit()
    return RedirectResponse("/admin", status_code=303)


# ---------- SEO-лендинги (модуль 4) ----------

def _service_for_task(db: Session, task: str) -> Service | None:
    for service in db.query(Service).filter(Service.is_active.is_(True)).all():
        try:
            if detect_task(service.title) == task:
                return service
        except RuntimeError:
            continue
    return None


@app.get("/uslugi")
def seo_index(request: Request, db: Session = Depends(get_db)):
    return render(request, db, "seo_index.html", pages=seo_pages.PAGES)


@app.get("/uslugi/{slug}")
def seo_landing(slug: str, request: Request, db: Session = Depends(get_db)):
    page = seo_pages.PAGES_BY_SLUG.get(slug)
    if page is None:
        raise HTTPException(404, "Страница не найдена")
    service = _service_for_task(db, page.task)
    related = [p for p in seo_pages.PAGES if p.task == page.task and p.slug != slug][:6]
    return render(
        request, db, "seo_landing.html",
        page=page, service=service, related=related,
    )


@app.get("/sitemap.xml")
def sitemap():
    base = config.PUBLIC_BASE_URL.rstrip("/")
    urls = [f"{base}/", f"{base}/uslugi"]
    urls += [f"{base}/uslugi/{p.slug}" for p in seo_pages.PAGES]
    body = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "".join(f"  <url><loc>{u}</loc></url>\n" for u in urls)
        + "</urlset>\n"
    )
    return Response(content=body, media_type="application/xml")


@app.get("/robots.txt")
def robots():
    base = config.PUBLIC_BASE_URL.rstrip("/")
    return PlainTextResponse(
        f"User-agent: *\nAllow: /\nSitemap: {base}/sitemap.xml\n"
    )


# ---------- админ: лиды авто-маркетинга (модуль 4) ----------

@app.get("/admin/leads")
def admin_leads(
    request: Request,
    source: str | None = None,
    db: Session = Depends(get_db),
):
    user = current_user(request, db)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    if not user.is_admin:
        raise HTTPException(403, "Доступ запрещён")
    query = db.query(Lead).order_by(Lead.created_at.desc())
    if source:
        query = query.filter(Lead.source == source)
    leads = query.limit(200).all()
    sources = [row[0] for row in db.query(Lead.source).distinct().all()]
    total = db.query(Lead).count()
    return render(
        request, db, "admin_leads.html",
        leads=leads,
        sources=sources,
        active_source=source,
        total=total,
        dry_run=config.MARKETING_DRY_RUN,
        enabled=config.MARKETING_ENABLED,
    )


@app.post("/admin/leads/run")
async def admin_leads_run(request: Request, db: Session = Depends(get_db)):
    require_admin(request, db)
    from .marketing import engine as marketing_engine
    await marketing_engine.run_cycle()
    return RedirectResponse("/admin/leads", status_code=303)


@app.post("/admin/retention/run")
async def admin_retention_run(request: Request, db: Session = Depends(get_db)):
    require_admin(request, db)
    from .retention import scheduler as retention_scheduler
    import anyio
    await anyio.to_thread.run_sync(retention_scheduler.run_cycle)
    return RedirectResponse("/admin", status_code=303)


# ---------- админ: финансовый дашборд (модуль 7) ----------

@app.get("/admin/finance")
def admin_finance(
    request: Request,
    refresh: int = 0,
    db: Session = Depends(get_db),
):
    user = current_user(request, db)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    if not user.is_admin:
        raise HTTPException(403, "Доступ запрещён")
    from . import finance
    stats = finance.get_stats(db, force=bool(refresh))
    return render(
        request, db, "admin_finance.html",
        stats=stats,
        cache_minutes=config.FINANCE_CACHE_SECONDS // 60,
    )
