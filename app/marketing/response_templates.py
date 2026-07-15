"""Шаблоны откликов — подбираются по типу найденного заказа."""

import random

from sqlalchemy.orm import Session

from .. import config
from ..models import Service
from .keywords import LEAD_KEYWORDS

_TEMPLATES: dict[str, list[str]] = {
    "text": [
        "Здравствуйте! Напишу текст под вашу задачу за 30 минут. "
        "Цена: {price}₽. Работаю с нейросетями + ручная вычитка. "
        "Примеры работ: {portfolio}",
        "Добрый день! Готов взять ваш текст в работу прямо сейчас, "
        "сдам через 30 минут. Стоимость {price}₽. Портфолио: {portfolio}",
    ],
    "image": [
        "Здравствуйте! Сгенерирую изображение по вашему описанию за 30 минут. "
        "Цена: {price}₽, правки включены. Примеры: {portfolio}",
        "Добрый день! Сделаю картинку под вашу задачу (Stable Diffusion, "
        "высокое качество) за 30 минут. {price}₽. Портфолио: {portfolio}",
    ],
    "background": [
        "Здравствуйте! Уберу фон с фото аккуратно и быстро — за 30 минут. "
        "Цена: {price}₽ за фото. Примеры: {portfolio}",
    ],
    "tts": [
        "Здравствуйте! Озвучу ваш текст естественным голосом за 30 минут. "
        "Цена: {price}₽. Пример звучания пришлю сразу. Портфолио: {portfolio}",
    ],
    "colorize": [
        "Здравствуйте! Раскрашу чёрно-белое фото реалистично за 30 минут. "
        "Цена: {price}₽. Примеры до/после: {portfolio}",
    ],
    "upscale": [
        "Здравствуйте! Увеличу разрешение фото в 4 раза без потери качества "
        "за 30 минут. Цена: {price}₽. Примеры: {portfolio}",
    ],
    "logo": [
        "Здравствуйте! Сделаю логотип: несколько вариантов на выбор, PNG + SVG. "
        "Срок — 30 минут, цена {price}₽. Портфолио: {portfolio}",
    ],
    "general": [
        "Здравствуйте! Занимаюсь задачами с нейросетями: тексты, картинки, "
        "логотипы, обработка фото, озвучка. Быстро и недорого (от {price}₽). "
        "Каталог услуг и цены: {portfolio}",
    ],
}

# соответствие типа задачи ключевым словам названия услуги в каталоге
_SERVICE_HINTS = {
    "text": "текст",
    "image": "изображен",
    "background": "фон",
    "tts": "озвуч",
    "colorize": "раскра",
    "upscale": "разреш",
    "logo": "логотип",
}


def build_response(task: str, db: Session) -> str:
    price = _price_for(task, db)
    template = random.choice(_TEMPLATES.get(task) or _TEMPLATES["general"])
    return template.format(price=price, portfolio=config.PORTFOLIO_URL)


def _price_for(task: str, db: Session) -> int:
    hint = _SERVICE_HINTS.get(task)
    if hint:
        for service in db.query(Service).filter(Service.is_active.is_(True)).all():
            if hint in service.title.lower():
                return service.price
    return 200  # «от 200₽» для общих запросов


assert set(_TEMPLATES) >= set(LEAD_KEYWORDS), "нет шаблона для какого-то типа лида"
