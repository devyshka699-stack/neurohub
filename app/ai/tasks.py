"""Автоматический выбор AI-задачи по названию услуги."""

# Порядок важен: «Озвучу текст» должно попасть в tts, а не в text
_KEYWORDS: list[tuple[tuple[str, ...], str]] = [
    (("фон",), "background"),
    (("озвуч", "голос"), "tts"),
    (("раскра", "колориз", "чёрно-бел", "черно-бел"), "colorize"),
    (("разреш", "увелич", "апскейл"), "upscale"),
    (("логотип", "лого"), "logo"),
    (("изображ", "картин", "сгенери"), "image"),
    (("текст", "стать", "напиш"), "text"),
]

TASK_LABELS = {
    "text": "генерация текста",
    "image": "генерация изображения",
    "background": "удаление фона",
    "tts": "озвучка текста",
    "colorize": "колоризация фото",
    "upscale": "увеличение разрешения",
    "logo": "создание логотипа",
}


def detect_task(service_title: str) -> str:
    title = service_title.lower()
    for keywords, task in _KEYWORDS:
        if any(k in title for k in keywords):
            return task
    raise RuntimeError(
        f"Не удалось определить тип AI-задачи для услуги «{service_title}». "
        "Выполните заказ вручную."
    )
