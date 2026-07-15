"""Ключевые слова для поиска заказов и сопоставления с нашими услугами."""

import re

# task -> список ключевых слов/фраз (в нижнем регистре)
TASK_KEYWORDS: dict[str, list[str]] = {
    "text": [
        "нужен текст", "написать текст", "написать статью", "копирайтер",
        "рерайт", "описание товара", "текст для сайта", "нужен копирайтинг",
        "напишите текст", "seo-текст", "продающий текст",
    ],
    "image": [
        "сгенерировать картинку", "нужна картинка", "нарисовать иллюстрацию",
        "сгенерировать изображение", "картинка по описанию", "арт на заказ",
        "нужна иллюстрация", "генерация изображений",
    ],
    "background": [
        "убрать фон", "удалить фон", "вырезать фон", "обработать фото",
        "почистить фон", "фон с фото", "прозрачный фон",
    ],
    "tts": [
        "озвучить текст", "озвучка видео", "нужна озвучка", "озвучить видео",
        "голос за кадром", "надиктовать текст", "закадровый голос",
    ],
    "colorize": [
        "раскрасить фото", "колоризация", "colorize", "раскрасить чёрно-белое",
        "восстановить старое фото",
    ],
    "upscale": [
        "увеличить разрешение", "улучшить качество фото", "апскейл",
        "повысить разрешение", "upscale", "сделать фото чётче",
    ],
    "logo": [
        "нужен логотип", "создать логотип", "разработать лого", "логотип для",
        "дизайн логотипа",
    ],
}

# Общие «сигнальные» фразы для соцсетей/чатов (широкий поиск исполнителя)
GENERIC_SIGNALS = [
    "кто может сделать", "нужен дизайнер", "кто умеет в нейросети",
    "ищу фрилансера", "нужен исполнитель", "кто может обработать фото",
    "кто может помочь с", "посоветуйте кто", "нужен человек который",
]


def _compile(words: list[str]) -> list[tuple[str, re.Pattern]]:
    out = []
    for w in words:
        out.append((w, re.compile(r"\b" + re.escape(w) + r"\b", re.IGNORECASE)))
    return out


_TASK_PATTERNS = {task: _compile(words) for task, words in TASK_KEYWORDS.items()}
_GENERIC_PATTERNS = _compile(GENERIC_SIGNALS)


def match(text: str) -> tuple[str | None, str | None]:
    """Возвращает (task, keyword) для первого совпадения или (None, None).

    Для generic-сигналов task = 'text' по умолчанию (самая частая услуга).
    """
    if not text:
        return None, None
    for task, patterns in _TASK_PATTERNS.items():
        for keyword, pattern in patterns:
            if pattern.search(text):
                return task, keyword
    for keyword, pattern in _GENERIC_PATTERNS:
        if pattern.search(text):
            return "text", keyword
    return None, None


def all_search_queries() -> list[str]:
    """Плоский список поисковых фраз для парсеров бирж."""
    queries: list[str] = []
    for words in TASK_KEYWORDS.values():
        queries.extend(words)
    return queries
