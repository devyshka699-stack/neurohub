from dataclasses import dataclass
from pathlib import Path


@dataclass
class AIResult:
    path: Path      # готовый файл (во временной папке задачи)
    filename: str   # имя файла для клиента
    comment: str    # комментарий к результату


IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff"}


def require_image(input_path: Path | None, what: str) -> Path:
    if input_path is None:
        raise RuntimeError(f"К заказу не приложено фото — {what} невозможно.")
    if input_path.suffix.lower() not in IMAGE_SUFFIXES:
        raise RuntimeError(
            f"Приложенный файл {input_path.suffix} не является изображением."
        )
    return input_path
