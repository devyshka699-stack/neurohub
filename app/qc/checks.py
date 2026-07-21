"""Конкретные проверки качества результата.

Все проверки деградируют мягко: если проверочная библиотека недоступна,
соответствующая метрика пропускается (не штрафует результат), а в отчёт
добавляется пометка.
"""

import logging
import re
import zipfile
from pathlib import Path

from .. import config
from .result import QCResult

log = logging.getLogger("qc")

_WORD_RE = re.compile(r"[А-Яа-яЁёA-Za-z]+")
_RU_STOP = {
    "и", "в", "во", "не", "что", "он", "на", "я", "с", "со", "как", "а", "то",
    "все", "она", "так", "его", "но", "да", "ты", "к", "у", "же", "вы", "за",
    "бы", "по", "только", "ее", "мне", "было", "вот", "от", "меня", "еще",
    "нет", "о", "из", "ему", "теперь", "когда", "даже", "ну", "вдруг", "ли",
    "если", "уже", "или", "ни", "быть", "был", "него", "до", "вас", "для",
    "это", "этот", "эта", "эти", "мы", "их", "ним", "чтобы", "их",
}


# ---------- текст ----------

def check_text(path: Path, description: str) -> QCResult:
    text = path.read_text(errors="ignore").strip() if path.exists() else ""
    checks: dict = {}
    issues: list[str] = []

    if not text:
        return QCResult(0, False, {"empty": True}, ["Пустой текст"], "Текст пустой")

    words = _WORD_RE.findall(text)
    word_count = len(words)
    checks["word_count"] = word_count

    # 1) орфография через pyspellchecker
    spell_score = _spelling_score(words, checks, issues)

    # 2) уникальность (эвристика: доля уникальных слов + отсутствие повторов фраз)
    uniqueness = _uniqueness_score(words, text, checks, issues)

    # 3) соответствие запросу (пересечение значимых слов запроса с текстом)
    relevance = _relevance_score(text, description, checks, issues)

    # 4) достаточный объём
    length_score = 100 if word_count >= 20 else max(0, word_count * 5)
    if word_count < 20:
        issues.append(f"Короткий текст ({word_count} слов)")

    # орфографии даём меньший вес: словарь pyspellchecker для русского неполный
    # и часто помечает валидные слова, поэтому это мягкий сигнал
    score = round(
        0.20 * spell_score
        + 0.30 * uniqueness
        + 0.30 * relevance
        + 0.20 * length_score
    )
    passed = score >= config.QC_MIN_SCORE
    note = f"Орфография {spell_score}, уникальность {uniqueness}, релевантность {relevance}"
    return QCResult(score, passed, checks, issues, note)


def _spelling_score(words, checks, issues) -> int:
    try:
        from spellchecker import SpellChecker
    except ImportError:
        checks["spelling"] = "pyspellchecker не установлен — пропущено"
        return 100

    ru_words = [w.lower() for w in words if re.fullmatch(r"[А-Яа-яЁё]+", w)]
    if not ru_words:
        checks["spelling"] = "нет русских слов для проверки"
        return 100
    try:
        spell = SpellChecker(language="ru")
    except Exception:
        checks["spelling"] = "русский словарь недоступен — пропущено"
        return 100

    check_words = [w for w in ru_words if w not in _RU_STOP]
    if not check_words:
        return 100
    misspelled = spell.unknown(check_words)
    ratio = len(misspelled) / len(check_words)
    checks["misspelled_ratio"] = round(ratio, 3)
    checks["misspelled_examples"] = list(sorted(misspelled))[:10]
    # порог повышен из-за неполноты русского словаря (много ложных срабатываний)
    if ratio > 0.35:
        issues.append(f"Много ошибок правописания ({int(ratio*100)}%)")
    # первые 10% «ошибок» не штрафуем — почти всегда ложные
    return max(0, round((1 - max(0, ratio - 0.1)) * 100))


def _uniqueness_score(words, text, checks, issues) -> int:
    if not words:
        return 0
    lower = [w.lower() for w in words]
    unique_ratio = len(set(lower)) / len(lower)
    checks["unique_word_ratio"] = round(unique_ratio, 3)

    # повторяющиеся 3-словные фразы — признак «воды»/зацикливания модели
    trigrams = [tuple(lower[i:i+3]) for i in range(len(lower) - 2)]
    repeated = 0
    if trigrams:
        seen = set()
        for t in trigrams:
            if t in seen:
                repeated += 1
            seen.add(t)
        repeat_ratio = repeated / len(trigrams)
    else:
        repeat_ratio = 0
    checks["repeated_trigram_ratio"] = round(repeat_ratio, 3)

    if unique_ratio < 0.4:
        issues.append("Низкая лексическая уникальность")
    if repeat_ratio > 0.1:
        issues.append("Повторяющиеся фразы в тексте")

    score = unique_ratio * 100 - repeat_ratio * 150
    return max(0, min(100, round(score)))


def _relevance_score(text, description, checks, issues) -> int:
    desc_words = {
        w.lower() for w in _WORD_RE.findall(description)
        if len(w) > 3 and w.lower() not in _RU_STOP
    }
    if not desc_words:
        checks["relevance"] = "нет ключевых слов в запросе"
        return 100
    text_words = {w.lower() for w in _WORD_RE.findall(text)}
    overlap = desc_words & text_words
    ratio = len(overlap) / len(desc_words)
    checks["relevance_overlap"] = round(ratio, 3)
    if ratio < 0.2:
        issues.append("Текст слабо связан с запросом")
    # мягкая шкала: даже частичное совпадение — уже неплохо
    return max(0, min(100, round(30 + ratio * 100)))


# ---------- изображения ----------

def check_image(path: Path, description: str) -> QCResult:
    # логотип приходит zip-архивом: извлекаем png для проверки
    work_path = path
    tmp = None
    if path.suffix.lower() == ".zip":
        work_path, tmp = _extract_png_from_zip(path)
        if work_path is None:
            return QCResult(
                0, False, {"zip": "нет PNG в архиве"},
                ["В архиве логотипа нет изображения"], "Некорректный архив"
            )

    try:
        from PIL import Image, ImageFilter, ImageStat
    except ImportError:
        return QCResult(
            100, True, {"pillow": "не установлен — проверка пропущена"},
            [], "Pillow недоступен, проверка изображения пропущена"
        )

    checks: dict = {}
    issues: list[str] = []
    try:
        img = Image.open(work_path)
        img.load()
    except Exception as exc:
        return QCResult(0, False, {"open_error": str(exc)}, ["Файл не открывается как изображение"], "Битый файл")

    w, h = img.size
    checks["size"] = f"{w}x{h}"

    # 1) размеры не нулевые и не крошечные
    size_ok = w >= 64 and h >= 64
    if not size_ok:
        issues.append(f"Слишком маленькое изображение {w}x{h}")

    # 2) пропорции в разумных пределах (не «полоска»)
    ratio = max(w, h) / min(w, h) if min(w, h) else 999
    checks["aspect_ratio"] = round(ratio, 2)
    ratio_ok = ratio <= 3.0
    if not ratio_ok:
        issues.append(f"Некорректные пропорции {round(ratio,2)}:1")

    rgb = img.convert("RGB")

    # 3) не пустое/не однотонное (детект «пустышки» и битой генерации)
    stat = ImageStat.Stat(rgb)
    stddev = sum(stat.stddev) / 3
    checks["color_stddev"] = round(stddev, 2)
    not_blank = stddev >= 8
    if not not_blank:
        issues.append("Изображение почти однотонное (возможен брак генерации)")

    # 4) резкость: дисперсия краёв (проще Лапласиана, без numpy)
    edges = rgb.convert("L").filter(ImageFilter.FIND_EDGES)
    sharp = ImageStat.Stat(edges).stddev[0]
    checks["sharpness"] = round(sharp, 2)
    not_blurry = sharp >= 12
    if not not_blurry:
        issues.append("Изображение выглядит размытым")

    if tmp is not None:
        tmp.cleanup()

    score = 0
    score += 25 if size_ok else 0
    score += 20 if ratio_ok else 0
    score += 30 if not_blank else 0
    score += 25 if not_blurry else 0

    # пустое/однотонное или крошечное изображение — почти наверняка брак генерации:
    # это критические дефекты, поэтому гарантированно не проходят порог
    if not not_blank or not size_ok:
        score = min(score, config.QC_MIN_SCORE - 1)

    passed = score >= config.QC_MIN_SCORE
    note = f"{w}x{h}, резкость {round(sharp,1)}, контраст {round(stddev,1)}"
    return QCResult(score, passed, checks, issues, note)


def _extract_png_from_zip(path: Path):
    import tempfile
    tmp = tempfile.TemporaryDirectory(prefix="qc_zip_")
    try:
        with zipfile.ZipFile(path) as zf:
            for name in zf.namelist():
                if name.lower().endswith((".png", ".jpg", ".jpeg")):
                    extracted = Path(tmp.name) / "img"
                    with zf.open(name) as src, open(extracted, "wb") as dst:
                        dst.write(src.read())
                    return extracted, tmp
    except Exception:
        pass
    tmp.cleanup()
    return None, None


# ---------- аудио ----------

def check_audio(path: Path, description: str) -> QCResult:
    checks: dict = {}
    issues: list[str] = []

    if not path.exists() or path.stat().st_size == 0:
        return QCResult(0, False, {"empty": True}, ["Пустой аудиофайл"], "Аудио пустое")
    checks["file_size"] = path.stat().st_size

    try:
        from pydub import AudioSegment
    except ImportError:
        # без pydub делаем базовую проверку по размеру файла
        ok = path.stat().st_size > 2000
        if not ok:
            issues.append("Аудиофайл подозрительно маленький")
        checks["pydub"] = "не установлен — глубокая проверка пропущена"
        return QCResult(
            85 if ok else 40, ok, checks, issues,
            "pydub недоступен, проверка по размеру файла"
        )

    # чтобы pydub не ругался и умел открывать mp3 напрямую, если захочет
    ffmpeg = _ffmpeg_exe()
    if ffmpeg:
        AudioSegment.converter = ffmpeg
        AudioSegment.ffprobe = ffmpeg

    audio = _decode_audio(path, AudioSegment)
    if audio is None:
        ok = path.stat().st_size > 2000
        checks["hint"] = "аудио не декодируется (нет ffmpeg?)"
        if not ok:
            issues.append("Аудио не декодируется и слишком маленькое")
        return QCResult(
            80 if ok else 30, ok, checks, issues,
            "ffmpeg недоступен, проверка по размеру файла"
        )

    duration = len(audio) / 1000.0
    checks["duration_sec"] = round(duration, 2)
    checks["dBFS"] = round(audio.dBFS, 2) if audio.dBFS != float("-inf") else None

    # 1) не пустое по длительности
    has_duration = duration >= 0.5
    if not has_duration:
        issues.append("Слишком короткое аудио")

    # 2) не тишина
    not_silent = audio.dBFS != float("-inf") and audio.dBFS > -50
    if not not_silent:
        issues.append("Аудио похоже на тишину")

    # 3) длительность соответствует объёму текста (~11 симв/сек речи)
    text_len = len(description.strip())
    expected = text_len / 11.0
    duration_ok = True
    if expected >= 2:
        lo, hi = expected * 0.35, expected * 2.5
        duration_ok = lo <= duration <= hi
        checks["expected_sec"] = round(expected, 1)
        if not duration_ok:
            issues.append(
                f"Длительность {round(duration,1)}с не соответствует объёму текста (~{round(expected,1)}с)"
            )

    score = 0
    score += 35 if has_duration else 0
    score += 40 if not_silent else 0
    score += 25 if duration_ok else 0
    passed = score >= config.QC_MIN_SCORE
    note = f"{round(duration,1)}с, громкость {checks['dBFS']} dBFS"
    return QCResult(score, passed, checks, issues, note)


def _ffmpeg_exe() -> str | None:
    """Порядок: FFMPEG_BIN из конфига → PATH → imageio-ffmpeg."""
    import shutil
    from .. import config

    if config.FFMPEG_BIN and Path(config.FFMPEG_BIN).exists():
        return config.FFMPEG_BIN
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        return None


def _decode_audio(path: Path, AudioSegment):
    """Декодирует аудио. mp3 конвертируется в wav через ffmpeg
    (pydub без системного ffmpeg умеет читать только wav)."""
    import subprocess
    import tempfile

    if path.suffix.lower() == ".wav":
        try:
            return AudioSegment.from_wav(path)
        except Exception:
            return None

    ffmpeg = _ffmpeg_exe()
    if ffmpeg is None:
        try:
            return AudioSegment.from_file(path)
        except Exception:
            return None

    with tempfile.TemporaryDirectory(prefix="qc_audio_") as tmp:
        wav = Path(tmp) / "audio.wav"
        proc = subprocess.run(
            [ffmpeg, "-y", "-i", str(path), str(wav)],
            capture_output=True, timeout=120,
        )
        if proc.returncode != 0 or not wav.exists():
            return None
        try:
            return AudioSegment.from_wav(wav)
        except Exception:
            return None


# ---------- базовая ----------

def check_generic(path: Path) -> QCResult:
    ok = path.exists() and path.stat().st_size > 0
    return QCResult(
        100 if ok else 0, ok,
        {"file_size": path.stat().st_size if path.exists() else 0},
        [] if ok else ["Пустой файл результата"],
        "Базовая проверка файла",
    )
