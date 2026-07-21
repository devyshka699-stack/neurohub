"""Колоризация чёрно-белых фото.

Основной путь — ComfyUI img2img (Stable Diffusion), уже поднятый для картинок.
Запасной — DeOldify, если задан DEOLDIFY_DIR и репозиторий установлен.
"""

import asyncio
import logging
import sys
from pathlib import Path

from ... import config
from .base import AIResult, require_image
from .image import comfy_img2img, to_english_prompt

log = logging.getLogger("ai.colorize")

_COLORIZE_PROMPT = (
    "colorize this black and white photograph, natural realistic colors, "
    "preserve composition and faces, photographic, detailed"
)
_COLORIZE_NEGATIVE = (
    "blurry, low quality, watermark, text, deformed, cartoon, oversaturated, "
    "changed composition, extra objects"
)

_DEOLDIFY_HINT = (
    "DeOldify не настроен. Для запасного пути:\n"
    "  git clone https://github.com/jantic/DeOldify ~/DeOldify\n"
    "  и задайте DEOLDIFY_DIR=~/DeOldify\n"
    "Основной путь — ComfyUI (должен быть запущен на COMFYUI_URL)."
)


def _deoldify(src: Path, dst: Path) -> None:
    deoldify_dir = Path(config.DEOLDIFY_DIR).expanduser()
    if not config.DEOLDIFY_DIR or not deoldify_dir.exists():
        raise RuntimeError(_DEOLDIFY_HINT)

    sys.path.insert(0, str(deoldify_dir))
    try:
        from deoldify import device
        from deoldify.device_id import DeviceId
        from deoldify.visualize import get_image_colorizer

        device.set(device=DeviceId.CPU)
        colorizer = get_image_colorizer(root_folder=deoldify_dir, artistic=True)
        result = colorizer.get_transformed_image(
            path=str(src), render_factor=35, watermarked=False
        )
        result.save(dst)
    except ImportError as exc:
        raise RuntimeError(f"{_DEOLDIFY_HINT}\n(ошибка импорта: {exc})")
    finally:
        if str(deoldify_dir) in sys.path:
            sys.path.remove(str(deoldify_dir))


async def run(
    description: str, input_path: Path | None, workdir: Path, attempt: int = 0
) -> AIResult:
    src = require_image(input_path, "колоризация")
    out = workdir / "colorized.png"

    # пользователь может уточнить цвета в описании — добавляем к базовому промпту
    extra = (description or "").strip()
    if extra:
        extra = await to_english_prompt(extra)
        prompt = f"{_COLORIZE_PROMPT}, {extra}"
    else:
        prompt = _COLORIZE_PROMPT

    # на повторных попытках чуть сильнее denoise — больше свободы модели
    denoise = min(0.55, 0.35 + 0.05 * attempt)
    steps = min(35, 20 + 5 * attempt)

    try:
        png = await comfy_img2img(
            src, prompt, negative=_COLORIZE_NEGATIVE, steps=steps, denoise=denoise
        )
        out.write_bytes(png)
        return AIResult(
            out, "colorized.png",
            f"Фото раскрашено (ComfyUI img2img, denoise={denoise:.2f})",
        )
    except Exception as comfy_exc:
        log.warning("ComfyUI colorize не удался: %s — пробуем DeOldify", comfy_exc)
        if not config.DEOLDIFY_DIR:
            raise RuntimeError(
                f"Колоризация через ComfyUI не удалась: {comfy_exc}\n"
                "Запустите ComfyUI или задайте DEOLDIFY_DIR для запасного пути."
            ) from comfy_exc
        await asyncio.to_thread(_deoldify, src, out)
        return AIResult(out, "colorized.png", "Фото раскрашено (DeOldify, artistic)")
