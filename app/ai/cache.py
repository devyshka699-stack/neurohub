"""Кэш результатов: ключ — хэш от типа задачи, описания и входного файла."""

import hashlib
import json
import shutil
from pathlib import Path

from .. import config


def make_key(task: str, description: str, file_bytes: bytes | None) -> str:
    h = hashlib.sha256()
    h.update(task.encode())
    h.update(b"\x00")
    h.update(description.strip().lower().encode())
    if file_bytes:
        h.update(b"\x00")
        h.update(file_bytes)
    return h.hexdigest()


def get(key: str) -> tuple[Path, str, str] | None:
    """Возвращает (путь_к_файлу, имя_файла, комментарий) или None."""
    meta_path = config.AI_CACHE_DIR / f"{key}.json"
    if not meta_path.exists():
        return None
    meta = json.loads(meta_path.read_text())
    file_path = config.AI_CACHE_DIR / meta["stored"]
    if not file_path.exists():
        return None
    return file_path, meta["filename"], meta["comment"]


def put(key: str, src: Path, filename: str, comment: str) -> None:
    stored = f"{key}{src.suffix}"
    shutil.copy2(src, config.AI_CACHE_DIR / stored)
    (config.AI_CACHE_DIR / f"{key}.json").write_text(
        json.dumps(
            {"stored": stored, "filename": filename, "comment": comment},
            ensure_ascii=False,
        )
    )
