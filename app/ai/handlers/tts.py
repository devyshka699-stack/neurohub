"""Озвучка текста: Edge TTS (бесплатный API Microsoft Edge)."""

from pathlib import Path

from ... import config
from .base import AIResult


async def run(
    description: str, input_path: Path | None, workdir: Path, attempt: int = 0
) -> AIResult:
    import edge_tts

    # если приложен .txt — озвучиваем его, иначе текст из описания заказа
    if input_path is not None and input_path.suffix.lower() in (".txt", ".md"):
        text = input_path.read_text(errors="ignore")
    else:
        text = description
    text = text.strip()
    if not text:
        raise RuntimeError("Пустой текст для озвучки")

    out = workdir / "speech.mp3"
    try:
        communicate = edge_tts.Communicate(text, config.EDGE_TTS_VOICE)
        await communicate.save(str(out))
    except Exception as exc:
        raise RuntimeError(f"Edge TTS не смог озвучить текст: {exc}")

    return AIResult(
        out, "speech.mp3", f"Текст озвучен (Edge TTS, голос {config.EDGE_TTS_VOICE})"
    )
