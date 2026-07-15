"""Генерация текста: Llama через Ollama (локально) с фолбэком на Groq API."""

from pathlib import Path

import httpx

from ... import config
from .base import AIResult

_PROMPT = (
    "Ты — профессиональный копирайтер. Выполни задачу клиента максимально "
    "качественно и по-русски (если клиент не просит другой язык). "
    "Ответь только готовым текстом, без пояснений и вступлений.\n\n"
    "Задача клиента: {description}"
)


async def run(
    description: str, input_path: Path | None, workdir: Path, attempt: int = 0
) -> AIResult:
    # если клиент приложил текстовый файл — добавляем его как материал
    extra = ""
    if input_path is not None and input_path.suffix.lower() in (".txt", ".md"):
        extra = f"\n\nМатериалы от клиента:\n{input_path.read_text(errors='ignore')[:8000]}"

    prompt = _PROMPT.format(description=description) + extra
    # при повторной генерации усиливаем требования к качеству и уникальности
    if attempt > 0:
        prompt += (
            "\n\nВАЖНО: пиши без орфографических ошибок, грамотно, "
            "избегай повторов и «воды», строго по теме запроса."
        )
    # температура растёт с попыткой — больше вариативности
    temperature = min(0.9, 0.6 + 0.15 * attempt)
    errors = []

    text, source = await _try_ollama(prompt, temperature, errors)
    if text is None:
        text, source = await _try_groq(prompt, temperature, errors)
    if text is None:
        raise RuntimeError(
            "Генерация текста недоступна. "
            f"Ollama: {errors[0]}. Groq: {errors[1]}. "
            "Запустите Ollama (`ollama serve && ollama pull llama3.2`) "
            "или задайте GROQ_API_KEY."
        )

    out = workdir / "result.txt"
    out.write_text(text.strip(), encoding="utf-8")
    return AIResult(out, "text.txt", f"Текст сгенерирован ({source})")


async def _try_ollama(prompt: str, temperature: float, errors: list) -> tuple[str | None, str]:
    try:
        async with httpx.AsyncClient(timeout=300) as client:
            resp = await client.post(
                f"{config.OLLAMA_URL}/api/generate",
                json={
                    "model": config.OLLAMA_MODEL,
                    "prompt": prompt,
                    "stream": False,
                    "options": {"temperature": temperature},
                },
            )
            resp.raise_for_status()
            return resp.json()["response"], f"{config.OLLAMA_MODEL} через Ollama"
    except Exception as exc:
        errors.append(str(exc) or type(exc).__name__)
        return None, ""


async def _try_groq(prompt: str, temperature: float, errors: list) -> tuple[str | None, str]:
    if not config.GROQ_API_KEY:
        errors.append("GROQ_API_KEY не задан")
        return None, ""
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {config.GROQ_API_KEY}"},
                json={
                    "model": config.GROQ_MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": temperature,
                },
            )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
            return content, f"{config.GROQ_MODEL} через Groq"
    except Exception as exc:
        errors.append(str(exc) or type(exc).__name__)
        return None, ""
