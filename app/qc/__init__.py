from pathlib import Path

from .result import QCResult
from . import checks


def run_qc(task: str, result_path: Path, description: str) -> QCResult:
    """Проверяет качество результата в зависимости от типа задачи.

    Для типов без специфичной проверки (например, увеличение разрешения)
    выполняется базовая проверка изображения.
    """
    if task in ("text",):
        return checks.check_text(result_path, description)
    if task in ("image", "logo", "background", "colorize", "upscale"):
        # logo может быть zip — обрабатывается внутри как исключение
        return checks.check_image(result_path, description)
    if task in ("tts",):
        return checks.check_audio(result_path, description)
    # неизвестный тип — не блокируем, просто проверяем, что файл не пустой
    return checks.check_generic(result_path)
