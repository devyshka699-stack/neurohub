"""SEO-генератор: 50+ лендингов под ключевые запросы.

Страницы строятся из шаблонов «интент × услуга»: каждая ведёт на форму заказа
соответствующей услуги. Контент детерминированный (без внешних вызовов), чтобы
страницы были стабильны для индексации.
"""

from dataclasses import dataclass

# task -> (человекочитаемое действие, id услуги в каталоге по порядку сидов)
_SERVICE_BY_TASK = {
    "background": "убрать фон с фото",
    "image": "сгенерировать картинку",
    "tts": "озвучить текст",
    "colorize": "раскрасить чёрно-белое фото",
    "upscale": "увеличить разрешение фото",
    "text": "написать текст",
    "logo": "создать логотип",
}

# Модификаторы интента — из них рождаются ключевые запросы
_MODIFIERS = [
    ("бесплатно", "бесплатно и без регистрации"),
    ("онлайн", "прямо в браузере, без установки программ"),
    ("нейросеть", "с помощью нейросети за пару минут"),
    ("быстро", "быстро — результат за 15–30 минут"),
    ("на русском", "полностью на русском языке"),
    ("без водяных знаков", "без водяных знаков и ограничений"),
    ("с телефона", "с телефона или компьютера"),
    ("недорого", "недорого и с гарантией результата"),
]

# Заголовки-шаблоны под ключевой запрос
_HEADINGS = [
    "{action} {modifier}",
    "Как {action_inf} {modifier}",
    "{action} онлайн {modifier}",
]


@dataclass
class SeoPage:
    slug: str
    keyword: str
    title: str
    h1: str
    intro: str
    task: str
    faq: list[tuple[str, str]]


def _slugify(text: str) -> str:
    table = str.maketrans(
        "абвгдеёжзийклмнопрстуфхцчшщъыьэюя ",
        "abvgdeejziyklmnoprstufhccss_y_eua-",
    )
    s = text.lower().translate(table)
    return "".join(ch for ch in s if ch.isalnum() or ch == "-").strip("-")


def _build_pages() -> list[SeoPage]:
    pages: list[SeoPage] = []
    seen: set[str] = set()
    for task, action in _SERVICE_BY_TASK.items():
        action_inf = action  # «убрать фон с фото»
        action_cap = action[0].upper() + action[1:]
        for kw_mod, human_mod in _MODIFIERS:
            keyword = f"{action} {kw_mod}"
            slug = _slugify(keyword)
            if slug in seen:
                continue
            seen.add(slug)
            title = f"{action_cap} {kw_mod} — сервис на нейросетях"
            h1 = f"{action_cap} {kw_mod}"
            intro = (
                f"Нужно {action_inf}? Наш сервис поможет {human_mod}. "
                f"Загрузите задачу — готовый результат придёт в течение получаса. "
                f"Работаем на современных нейросетях, оплата картой, криптовалютой "
                f"или через Telegram."
            )
            faq = [
                (
                    f"Сколько стоит {action_inf}?",
                    "Фиксированная цена указана в каталоге, скрытых платежей нет.",
                ),
                (
                    f"Как быстро можно {action_inf}?",
                    "Обычно результат готов за 15–30 минут после оплаты.",
                ),
                (
                    "Нужна ли регистрация?",
                    "Достаточно оформить заказ на сайте или через Telegram-бота.",
                ),
            ]
            pages.append(SeoPage(slug, keyword, title, h1, intro, task, faq))
    return pages


PAGES: list[SeoPage] = _build_pages()
PAGES_BY_SLUG: dict[str, SeoPage] = {p.slug: p for p in PAGES}
