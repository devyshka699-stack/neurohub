"""Логотипы: Stable Diffusion (ComfyUI) + векторизация в SVG через vtracer."""

import asyncio
import zipfile
from pathlib import Path

from .base import AIResult
from .image import comfy_txt2img, to_english_prompt

_PROMPT = (
    "minimalist vector logo design, {description}, flat design, simple geometric "
    "shapes, clean lines, white background, professional branding, high quality"
)
_NEGATIVE = "photo, realistic, 3d render, text, letters, watermark, blurry, complex"


def _vectorize(png: Path, svg: Path) -> None:
    import vtracer

    vtracer.convert_image_to_svg_py(
        str(png), str(svg), colormode="color", filter_speckle=8
    )


async def run(
    description: str, input_path: Path | None, workdir: Path, attempt: int = 0
) -> AIResult:
    prompt = await to_english_prompt(description)
    png_bytes = await comfy_txt2img(
        prompt=_PROMPT.format(description=prompt),
        negative=_NEGATIVE,
        width=768,
        height=768,
        steps=min(40, 25 + 5 * attempt),
    )
    png = workdir / "logo.png"
    png.write_bytes(png_bytes)

    svg = workdir / "logo.svg"
    vector_note = ""
    try:
        await asyncio.to_thread(_vectorize, png, svg)
    except Exception as exc:
        svg = None
        vector_note = f" (векторизация не удалась: {exc}, приложен только PNG)"

    out = workdir / "logo.zip"
    with zipfile.ZipFile(out, "w") as zf:
        zf.write(png, "logo.png")
        if svg is not None and svg.exists():
            zf.write(svg, "logo.svg")

    return AIResult(
        out, "logo.zip",
        "Логотип: Stable Diffusion + векторизация vtracer, PNG и SVG в архиве"
        + vector_note,
    )
