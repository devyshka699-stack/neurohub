"""Удаление фона с фото: библиотека rembg (локально)."""

import asyncio
from pathlib import Path

from .base import AIResult, require_image


def _remove_bg(src: Path, dst: Path) -> None:
    from rembg import remove  # ленивый импорт: при первом вызове качает веса u2net

    dst.write_bytes(remove(src.read_bytes()))


async def run(
    description: str, input_path: Path | None, workdir: Path, attempt: int = 0
) -> AIResult:
    src = require_image(input_path, "удаление фона")
    out = workdir / "no_background.png"
    try:
        await asyncio.to_thread(_remove_bg, src, out)
    except ImportError:
        raise RuntimeError(
            "Библиотека rembg не установлена. Выполните: pip install \"rembg[cpu]\""
        )
    return AIResult(
        out, "no_background.png", "Фон удалён (rembg, модель u2net), прозрачный PNG"
    )
