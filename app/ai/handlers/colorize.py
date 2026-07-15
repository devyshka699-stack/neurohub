"""Колоризация чёрно-белых фото: DeOldify (опенсорсная модель)."""

import asyncio
import sys
from pathlib import Path

from ... import config
from .base import AIResult, require_image

_INSTALL_HINT = (
    "DeOldify не настроен. Установка:\n"
    "  git clone https://github.com/jantic/DeOldify ~/DeOldify\n"
    "  pip install -r ~/DeOldify/requirements.txt\n"
    "  скачайте веса ColorizeArtistic_gen.pth в ~/DeOldify/models/\n"
    "  export DEOLDIFY_DIR=~/DeOldify\n"
    "Либо выполните заказ вручную."
)


def _colorize(src: Path, dst: Path) -> None:
    deoldify_dir = Path(config.DEOLDIFY_DIR).expanduser()
    if not config.DEOLDIFY_DIR or not deoldify_dir.exists():
        raise RuntimeError(_INSTALL_HINT)

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
        raise RuntimeError(f"{_INSTALL_HINT}\n(ошибка импорта: {exc})")
    finally:
        sys.path.remove(str(deoldify_dir))


async def run(
    description: str, input_path: Path | None, workdir: Path, attempt: int = 0
) -> AIResult:
    src = require_image(input_path, "колоризация")
    out = workdir / "colorized.png"
    await asyncio.to_thread(_colorize, src, out)
    return AIResult(out, "colorized.png", "Фото раскрашено (DeOldify, artistic)")
