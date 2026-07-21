"""Генерация изображений: Stable Diffusion через ComfyUI API."""

import asyncio
import logging
import random
import re
from pathlib import Path

import httpx

from ... import config
from .base import AIResult

log = logging.getLogger("ai.image")

NEGATIVE = "blurry, low quality, watermark, text, deformed, ugly"

_CYRILLIC = re.compile("[А-Яа-яЁё]")


async def to_english_prompt(description: str) -> str:
    """Переводит описание на английский: SD 1.5 почти не понимает русский.

    Перевод через Ollama; если она недоступна — возвращаем как есть
    (лучше плохой промпт, чем упавший заказ).
    """
    if not _CYRILLIC.search(description):
        return description
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{config.OLLAMA_URL}/api/generate",
                json={
                    "model": config.OLLAMA_MODEL,
                    "prompt": (
                        "Translate this image description from Russian to English. "
                        "Reply with ONLY the translation, no explanations, "
                        f"no quotes:\n\n{description}"
                    ),
                    "stream": False,
                    "options": {"temperature": 0.2},
                },
            )
            resp.raise_for_status()
            text = resp.json().get("response", "").strip().strip('"')
            # модель иногда добавляет пояснения — берём первую непустую строку
            first_line = next(
                (ln.strip() for ln in text.splitlines() if ln.strip()), ""
            )
            if first_line and not _CYRILLIC.search(first_line):
                log.info("Промпт переведён: %r -> %r", description, first_line)
                return first_line
    except Exception:
        log.warning("Перевод промпта недоступен, используем оригинал")
    return description


async def run(
    description: str, input_path: Path | None, workdir: Path, attempt: int = 0
) -> AIResult:
    prompt = await to_english_prompt(description)
    # на повторных попытках повышаем число шагов для лучшего качества
    steps = min(40, 25 + 5 * attempt)
    png = await comfy_txt2img(
        prompt=f"{prompt}, high quality, detailed, masterpiece",
        negative=NEGATIVE,
        steps=steps,
    )
    out = workdir / "image.png"
    out.write_bytes(png)
    return AIResult(
        out, "image.png",
        f"Изображение сгенерировано (Stable Diffusion, {config.COMFYUI_CHECKPOINT})",
    )


def _comfy_unavailable() -> RuntimeError:
    return RuntimeError(
        f"ComfyUI недоступен по адресу {config.COMFYUI_URL}. "
        "Запустите ComfyUI (python main.py --listen) с моделью "
        f"{config.COMFYUI_CHECKPOINT} и повторите."
    )


async def _comfy_submit(workflow: dict) -> str:
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{config.COMFYUI_URL}/prompt", json={"prompt": workflow}
            )
            resp.raise_for_status()
            return resp.json()["prompt_id"]
    except (httpx.ConnectError, httpx.ConnectTimeout):
        raise _comfy_unavailable()


async def _comfy_wait_png(prompt_id: str) -> bytes:
    """Ждёт завершения workflow и возвращает PNG-байты (до 10 минут)."""
    async with httpx.AsyncClient(timeout=30) as client:
        for _ in range(600):
            await asyncio.sleep(1)
            hist = (await client.get(f"{config.COMFYUI_URL}/history/{prompt_id}")).json()
            if prompt_id not in hist:
                continue
            entry = hist[prompt_id]
            status = entry.get("status", {})
            if status.get("status_str") == "error":
                raise RuntimeError("ComfyUI вернул ошибку при генерации")
            images = entry.get("outputs", {}).get("save", {}).get("images")
            if images:
                img = images[0]
                view = await client.get(
                    f"{config.COMFYUI_URL}/view",
                    params={
                        "filename": img["filename"],
                        "subfolder": img.get("subfolder", ""),
                        "type": img.get("type", "output"),
                    },
                )
                view.raise_for_status()
                return view.content
        raise RuntimeError("ComfyUI не завершил генерацию за 10 минут")


async def comfy_upload_image(path: Path) -> str:
    """Загружает файл в ComfyUI input/ и возвращает имя для LoadImage."""
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            with path.open("rb") as f:
                resp = await client.post(
                    f"{config.COMFYUI_URL}/upload/image",
                    files={"image": (path.name, f, "application/octet-stream")},
                    data={"overwrite": "true"},
                )
            resp.raise_for_status()
            return resp.json()["name"]
    except (httpx.ConnectError, httpx.ConnectTimeout):
        raise _comfy_unavailable()


async def comfy_txt2img(
    prompt: str, negative: str = NEGATIVE, width: int = 768, height: int = 768,
    steps: int = 25,
) -> bytes:
    """Отправляет txt2img workflow в ComfyUI и возвращает PNG-байты."""
    prompt_id = await _comfy_submit(_txt2img_workflow(prompt, negative, width, height, steps))
    return await _comfy_wait_png(prompt_id)


async def comfy_img2img(
    image_path: Path,
    prompt: str,
    negative: str = NEGATIVE,
    steps: int = 25,
    denoise: float = 0.4,
) -> bytes:
    """img2img: загружает исходник, слегка перегенерирует latent (колоризация и т.п.)."""
    uploaded = await comfy_upload_image(image_path)
    prompt_id = await _comfy_submit(
        _img2img_workflow(uploaded, prompt, negative, steps, denoise)
    )
    return await _comfy_wait_png(prompt_id)


def _txt2img_workflow(
    prompt: str, negative: str, width: int, height: int, steps: int = 25
) -> dict:
    return {
        "ckpt": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": config.COMFYUI_CHECKPOINT},
        },
        "pos": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": prompt, "clip": ["ckpt", 1]},
        },
        "neg": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": negative, "clip": ["ckpt", 1]},
        },
        "latent": {
            "class_type": "EmptyLatentImage",
            "inputs": {"width": width, "height": height, "batch_size": 1},
        },
        "sampler": {
            "class_type": "KSampler",
            "inputs": {
                "model": ["ckpt", 0],
                "positive": ["pos", 0],
                "negative": ["neg", 0],
                "latent_image": ["latent", 0],
                "seed": random.randint(0, 2**32 - 1),
                "steps": steps,
                "cfg": 7.0,
                "sampler_name": "euler",
                "scheduler": "normal",
                "denoise": 1.0,
            },
        },
        "decode": {
            "class_type": "VAEDecode",
            "inputs": {"samples": ["sampler", 0], "vae": ["ckpt", 2]},
        },
        "save": {
            "class_type": "SaveImage",
            "inputs": {"images": ["decode", 0], "filename_prefix": "ai_shop"},
        },
    }


def _img2img_workflow(
    image_name: str, prompt: str, negative: str, steps: int, denoise: float
) -> dict:
    return {
        "ckpt": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": config.COMFYUI_CHECKPOINT},
        },
        "load": {
            "class_type": "LoadImage",
            "inputs": {"image": image_name},
        },
        "encode": {
            "class_type": "VAEEncode",
            "inputs": {"pixels": ["load", 0], "vae": ["ckpt", 2]},
        },
        "pos": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": prompt, "clip": ["ckpt", 1]},
        },
        "neg": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": negative, "clip": ["ckpt", 1]},
        },
        "sampler": {
            "class_type": "KSampler",
            "inputs": {
                "model": ["ckpt", 0],
                "positive": ["pos", 0],
                "negative": ["neg", 0],
                "latent_image": ["encode", 0],
                "seed": random.randint(0, 2**32 - 1),
                "steps": steps,
                "cfg": 7.0,
                "sampler_name": "euler",
                "scheduler": "normal",
                "denoise": denoise,
            },
        },
        "decode": {
            "class_type": "VAEDecode",
            "inputs": {"samples": ["sampler", 0], "vae": ["ckpt", 2]},
        },
        "save": {
            "class_type": "SaveImage",
            "inputs": {"images": ["decode", 0], "filename_prefix": "ai_shop_i2i"},
        },
    }
