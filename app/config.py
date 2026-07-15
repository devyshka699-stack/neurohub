import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# Настройки читаются из файла .env в корне проекта (если он есть),
# переменные окружения имеют приоритет над .env
try:
    from dotenv import load_dotenv
    load_dotenv(BASE_DIR / ".env")
except ImportError:
    pass

SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-change-me")

# Название магазина и контакт поддержки (показываются в шапке, футере и на оплате)
SHOP_NAME = os.getenv("SHOP_NAME", "AI-услуги")
SUPPORT_CONTACT = os.getenv("SUPPORT_CONTACT", "")  # например @username или email

# Учётка администратора создаётся при первом запуске
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "admin@example.com")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")

# Реквизиты для оплаты (показываются клиенту на странице оплаты)
PAYMENT_CARD_NUMBER = os.getenv("PAYMENT_CARD_NUMBER", "2200 0000 0000 0000")
PAYMENT_CARD_HOLDER = os.getenv("PAYMENT_CARD_HOLDER", "IVAN IVANOV")
PAYMENT_CARD_BANK = os.getenv("PAYMENT_CARD_BANK", "Т-Банк")
# Пустое значение = кошелёк не настроен, валюта скрывается на странице оплаты
PAYMENT_CRYPTO_USDT_TRC20 = os.getenv("PAYMENT_CRYPTO_USDT_TRC20", "")
PAYMENT_CRYPTO_BTC = os.getenv("PAYMENT_CRYPTO_BTC", "")

# Переопределяется для деплоя с постоянным диском (например /var/data/uploads)
UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", BASE_DIR / "uploads"))
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

MAX_UPLOAD_SIZE = 20 * 1024 * 1024  # 20 МБ

# ---------- AI backend (модуль 2) ----------

# Кэш результатов: одинаковые запросы не обрабатываются дважды
AI_CACHE_DIR = BASE_DIR / "ai_cache"
AI_CACHE_DIR.mkdir(exist_ok=True)

# Автоматически запускать AI-обработку после подтверждения оплаты
AI_AUTO_PROCESS = os.getenv("AI_AUTO_PROCESS", "1") == "1"

# Тексты: Ollama (локально) с фолбэком на Groq API
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2:latest")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.1-8b-instant")

# Изображения и логотипы: Stable Diffusion через ComfyUI
COMFYUI_URL = os.getenv("COMFYUI_URL", "http://127.0.0.1:8188")
COMFYUI_CHECKPOINT = os.getenv("COMFYUI_CHECKPOINT", "v1-5-pruned-emaonly.safetensors")

# Озвучка: Edge TTS
EDGE_TTS_VOICE = os.getenv("EDGE_TTS_VOICE", "ru-RU-DmitryNeural")

# Апскейл: Real-ESRGAN (ncnn-vulkan бинарник)
REALESRGAN_BIN = os.getenv("REALESRGAN_BIN", "")

# Колоризация: DeOldify (папка с клонированным репозиторием и весами)
DEOLDIFY_DIR = os.getenv("DEOLDIFY_DIR", "")

# ---------- Telegram-бот (модуль 3) ----------

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")

# Курс Telegram Stars: сколько рублей стоит 1 звезда (для пересчёта цен)
TG_STARS_RATE_RUB = float(os.getenv("TG_STARS_RATE_RUB", "1.8"))

# Реферальный бонус в рублях за приведённого друга (начисляется после
# первой оплаты друга)
REFERRAL_BONUS = int(os.getenv("REFERRAL_BONUS", "50"))

# ---------- Auto-marketing engine (модуль 4) ----------

# Главный рубильник маркетингового движка (планировщик + парсеры)
MARKETING_ENABLED = os.getenv("MARKETING_ENABLED", "0") == "1"

# БЕЗОПАСНОСТЬ: в режиме dry-run отклики только формируются и сохраняются в БД,
# но НЕ отправляются на площадки. Реальная отправка включается осознанно.
MARKETING_DRY_RUN = os.getenv("MARKETING_DRY_RUN", "1") == "1"

# Интервал проверки новых заказов, секунды
MARKETING_INTERVAL = int(os.getenv("MARKETING_INTERVAL", "300"))  # 5 минут

# Антиспам: минимальный интервал между откликами на одну площадку, секунды
MARKETING_MIN_REPLY_INTERVAL = int(os.getenv("MARKETING_MIN_REPLY_INTERVAL", "60"))

# Публичный адрес сайта — для ссылок в откликах и SEO sitemap.
# На Render подхватывается автоматически из RENDER_EXTERNAL_URL.
PUBLIC_BASE_URL = (
    os.getenv("PUBLIC_BASE_URL")
    or os.getenv("RENDER_EXTERNAL_URL")
    or "http://127.0.0.1:8017"
).rstrip("/")

# Ссылка на портфолио для откликов
PORTFOLIO_URL = os.getenv("PORTFOLIO_URL", PUBLIC_BASE_URL)

# ---------- Client retention system (модуль 5) ----------

# Включить планировщик рассылок удержания
RETENTION_ENABLED = os.getenv("RETENTION_ENABLED", "1") == "1"

# Как часто планировщик проверяет клиентов (секунды)
RETENTION_INTERVAL = int(os.getenv("RETENTION_INTERVAL", "3600"))  # раз в час

# Промокод после выполненного заказа
PROMO_POST_ORDER_PERCENT = int(os.getenv("PROMO_POST_ORDER_PERCENT", "30"))
PROMO_POST_ORDER_HOURS = int(os.getenv("PROMO_POST_ORDER_HOURS", "48"))

# Win-back: клиент не заказывал N дней → предложение с промокодом
WINBACK_DAYS = int(os.getenv("WINBACK_DAYS", "7"))
WINBACK_PERCENT = int(os.getenv("WINBACK_PERCENT", "50"))
# Не чаще одного win-back в столько дней (чтобы не спамить неактивных)
WINBACK_COOLDOWN_DAYS = int(os.getenv("WINBACK_COOLDOWN_DAYS", "14"))

# VIP-порог: сколько оплаченных заказов даёт персонального менеджера
VIP_ORDERS_THRESHOLD = int(os.getenv("VIP_ORDERS_THRESHOLD", "5"))

# Ежемесячная рассылка: раз в столько дней
NEWSLETTER_EVERY_DAYS = int(os.getenv("NEWSLETTER_EVERY_DAYS", "30"))

# ---------- Quality control (модуль 6) ----------

# Включить автопроверку качества результата
QC_ENABLED = os.getenv("QC_ENABLED", "1") == "1"

# Минимальный балл качества (0-100) для прохождения автопроверки
QC_MIN_SCORE = int(os.getenv("QC_MIN_SCORE", "70"))

# Сколько раз перегенерировать результат при низком качестве
QC_MAX_ATTEMPTS = int(os.getenv("QC_MAX_ATTEMPTS", "3"))

# Требовать финальное подтверждение человеком после пройденной автопроверки.
# Если 0 — результат уходит клиенту сразу после автопроверки.
QC_REQUIRE_APPROVAL = os.getenv("QC_REQUIRE_APPROVAL", "1") == "1"

# ---------- Financial dashboard (модуль 7) ----------

# Как долго кэшируется статистика дашборда (секунды), по умолчанию час
FINANCE_CACHE_SECONDS = int(os.getenv("FINANCE_CACHE_SECONDS", "3600"))

# ---------- Auto-marketing engine: доп. настройки парсеров ----------

# Автоотклик. По умолчанию выключен: лиды и готовые тексты откликов
# копятся в админке, отправляете вручную. Включение — на ваш риск
# (площадки банят за автоматические отклики).
MARKETING_AUTO_RESPOND = os.getenv("MARKETING_AUTO_RESPOND", "0") == "1"

# Антиспам: не более 1 отклика в минуту на одну площадку
MARKETING_RATE_LIMIT = int(os.getenv("MARKETING_RATE_LIMIT", "60"))  # секунд

# Какие парсеры включены
KWORK_ENABLED = os.getenv("KWORK_ENABLED", "1") == "1"
YOUDO_ENABLED = os.getenv("YOUDO_ENABLED", "1") == "1"

# VK: поиск свежих постов/комментариев по ключевым фразам (нужен токен
# с доступом к newsfeed.search — https://vkhost.github.io)
VK_TOKEN = os.getenv("VK_TOKEN", "")

# Мониторинг Telegram-чатов через Telethon (пользовательский аккаунт).
# Получите api_id/api_hash на https://my.telegram.org, затем один раз
# выполните: python -m app.marketing.tg_login
TG_API_ID = int(os.getenv("TG_API_ID", "0"))
TG_API_HASH = os.getenv("TG_API_HASH", "")
TG_SESSION = str(BASE_DIR / "tg_monitor.session")
# список чатов через запятую: @chat1,@chat2
TG_MONITOR_CHATS = [
    c.strip() for c in os.getenv("TG_MONITOR_CHATS", "").split(",") if c.strip()
]
