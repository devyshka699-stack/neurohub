"""Генерация изображений: Stable Diffusion через ComfyUI API."""

import asyncio
import random
from pathlib import Path

import httpx

from ... import config
from .base import AIResult

NEGATIVE = "blurry, low quality, watermark, text, deformed, ugly"


async def run(
    description: str, input_path: Path | None, workdir: Path, attempt: int = 0
) -> AIResult:
    # на повторных попытках повышаем число шагов для лучшего качества
    steps = min(40, 25 + 5 * attempt)
    png = await comfy_txt2img(
        prompt=f"{description}, high quality, detailed, masterpiece",
        negative=NEGATIVE,
        steps=steps,
    )
    out = workdir / "image.png"
    out.write_bytes(png)
    return AIResult(
        out, "image.png",
        f"Изображение сгенерировано (Stable Diffusion, {config.COMFYUI_CHECKPOINT})",
    )


async def comfy_txt2img(
    prompt: str, negative: str = NEGATIVE, width: int = 768, height: int = 768,
    steps: int = 25,
) -> bytes:
    """Отправляет workflow в ComfyUI и возвращает PNG-байты."""
    workflow = _workflow(prompt, negative, width, height, steps)
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{config.COMFYUI_URL}/prompt", json={"prompt": workflow}
            )
            resp.raise_for_status()
            prompt_id = resp.json()["prompt_id"]
    except (httpx.ConnectError, httpx.ConnectTimeout):
        raise RuntimeError(
            f"ComfyUI недоступен по адресу {config.COMFYUI_URL}. "
            "Запустите ComfyUI (python main.py --listen) с моделью "
            f"{config.COMFYUI_CHECKPOINT} и повторите."
        )

    # ждём завершения генерации (до 10 минут)
    async with httpx.AsyncClient(timeout=30) as client:
        for _ in range(600):
            await asyncio.sleep(1)
            hist = (await client.get(f"{config.COMFYUI_URL}/history/{prompt_id}")).json()
            if prompt_id in hist:
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


def _workflow(prompt: str, negative: str, width: int, height: int, steps: int = 25) -> dict:
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
