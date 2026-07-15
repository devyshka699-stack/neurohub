from sqlalchemy.orm import Session

from . import config
from .models import Service, User
from .security import hash_password

SERVICES = [
    {
        "title": "Напишу текст для чего угодно",
        "description": "Статьи, посты, описания товаров, письма, сценарии — любой текст под вашу задачу.",
        "price": 500,
        "icon": "✍️",
    },
    {
        "title": "Сгенерирую изображение по описанию",
        "description": "Уникальная картинка по вашему текстовому описанию: арт, иллюстрация, обложка.",
        "price": 300,
        "icon": "🎨",
    },
    {
        "title": "Уберу фон с фото",
        "description": "Чистое вырезание объекта с фотографии. Прозрачный или любой другой фон.",
        "price": 200,
        "icon": "✂️",
    },
    {
        "title": "Озвучу текст голосом",
        "description": "Превращу ваш текст в аудио с естественным голосом. Формат MP3/WAV.",
        "price": 400,
        "icon": "🎙️",
    },
    {
        "title": "Раскрашу чёрно-белое фото",
        "description": "Реалистичная колоризация старых чёрно-белых фотографий.",
        "price": 350,
        "icon": "🌈",
    },
    {
        "title": "Увеличу разрешение фото",
        "description": "Апскейл изображения в 2–4 раза без потери качества, устранение шумов.",
        "price": 250,
        "icon": "🔍",
    },
    {
        "title": "Создам логотип",
        "description": "Уникальный логотип для вашего бренда: несколько вариантов на выбор.",
        "price": 600,
        "icon": "💎",
    },
]


DEFAULT_ADMIN_EMAIL = "admin@example.com"


def seed(db: Session) -> None:
    if db.query(Service).count() == 0:
        for item in SERVICES:
            db.add(Service(**item))

    _sync_admin(db)
    db.commit()


def _sync_admin(db: Session) -> None:
    """Создаёт/обновляет админа по ADMIN_EMAIL и ADMIN_PASSWORD из .env.

    Работает при каждом запуске: смена пароля или почты в .env
    применяется после перезапуска сервера.
    """
    admin = db.query(User).filter(User.email == config.ADMIN_EMAIL).first()
    if admin is None:
        db.add(
            User(
                email=config.ADMIN_EMAIL,
                name="Администратор",
                password_hash=hash_password(config.ADMIN_PASSWORD),
                is_admin=True,
            )
        )
    else:
        admin.is_admin = True
        new_hash = hash_password(config.ADMIN_PASSWORD)
        from .security import verify_password
        if not verify_password(config.ADMIN_PASSWORD, admin.password_hash):
            admin.password_hash = new_hash

    # если админ переехал на новую почту — забираем права у дефолтной учётки
    if config.ADMIN_EMAIL != DEFAULT_ADMIN_EMAIL:
        old = db.query(User).filter(User.email == DEFAULT_ADMIN_EMAIL).first()
        if old is not None:
            old.is_admin = False
