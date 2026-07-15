from pathlib import Path

from .base import AIResult
from . import background, colorize, image, logo, text, tts, upscale

_HANDLERS = {
    "text": text.run,
    "image": image.run,
    "background": background.run,
    "tts": tts.run,
    "colorize": colorize.run,
    "upscale": upscale.run,
    "logo": logo.run,
}


async def run_handler(
    task: str, description: str, input_path: Path | None, workdir: Path,
    attempt: int = 0,
) -> AIResult:
    handler = _HANDLERS.get(task)
    if handler is None:
        raise RuntimeError(f"Нет обработчика для задачи «{task}»")
    # attempt > 0 — повторная генерация после неудачной проверки качества:
    # обработчик может варьировать параметры (температуру, seed и т.п.)
    return await handler(description, input_path, workdir, attempt=attempt)
