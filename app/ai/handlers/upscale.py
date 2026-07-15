"""Увеличение разрешения фото: Real-ESRGAN (ncnn-vulkan, работает на Apple Silicon)."""

import asyncio
import shutil
import subprocess
from pathlib import Path

from ... import config
from .base import AIResult, require_image

_INSTALL_HINT = (
    "Real-ESRGAN не настроен. Скачайте realesrgan-ncnn-vulkan для macOS: "
    "https://github.com/xinntao/Real-ESRGAN/releases "
    "(архив realesrgan-ncnn-vulkan-*-macos.zip), распакуйте и задайте "
    "REALESRGAN_BIN=/путь/к/realesrgan-ncnn-vulkan. Либо выполните заказ вручную."
)


def _find_bin() -> str:
    if config.REALESRGAN_BIN and Path(config.REALESRGAN_BIN).exists():
        return config.REALESRGAN_BIN
    found = shutil.which("realesrgan-ncnn-vulkan")
    if found:
        return found
    raise RuntimeError(_INSTALL_HINT)


async def run(
    description: str, input_path: Path | None, workdir: Path, attempt: int = 0
) -> AIResult:
    src = require_image(input_path, "увеличение разрешения")
    binary = _find_bin()
    out = workdir / "upscaled.png"

    proc = await asyncio.to_thread(
        subprocess.run,
        [binary, "-i", str(src), "-o", str(out), "-n", "realesrgan-x4plus"],
        capture_output=True,
        text=True,
        timeout=600,
    )
    if proc.returncode != 0 or not out.exists():
        raise RuntimeError(f"Real-ESRGAN завершился с ошибкой: {proc.stderr[-500:]}")

    return AIResult(
        out, "upscaled.png", "Разрешение увеличено в 4 раза (Real-ESRGAN x4plus)"
    )
