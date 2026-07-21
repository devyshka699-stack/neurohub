"""Локальный воркер: забирает заказы с Render и обрабатывает AI на Маке.

Запуск (Mac, пока открыт ComfyUI/Ollama по необходимости):

    cd ai-services-shop
    # в .env: WORKER_BASE_URL + WORKER_TOKEN
    .venv/bin/python -m app.worker

На Render в Environment: тот же WORKER_TOKEN и REMOTE_WORKER=1.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import tempfile
from pathlib import Path

import httpx

from . import config
from .ai.queue import _generate_with_qc
from .ai.tasks import detect_task

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [worker] %(message)s",
)
log = logging.getLogger("worker")


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {config.WORKER_TOKEN}"}


def _base() -> str:
    if not config.WORKER_BASE_URL:
        raise SystemExit(
            "Задайте WORKER_BASE_URL в .env (например https://neurohub-hjs6.onrender.com)"
        )
    if not config.WORKER_TOKEN:
        raise SystemExit("Задайте WORKER_TOKEN в .env (тот же, что на Render)")
    return config.WORKER_BASE_URL


async def fetch_pending(client: httpx.AsyncClient) -> list[dict]:
    r = await client.get(f"{_base()}/api/worker/orders/pending", headers=_headers())
    r.raise_for_status()
    return r.json().get("orders", [])


async def claim(client: httpx.AsyncClient, order_id: int) -> bool:
    r = await client.post(
        f"{_base()}/api/worker/orders/{order_id}/claim", headers=_headers()
    )
    if r.status_code == 409:
        log.info("Заказ %s уже занят", order_id)
        return False
    r.raise_for_status()
    return True


async def download_file(
    client: httpx.AsyncClient, order_id: int, dest: Path, file_name: str | None
) -> Path | None:
    r = await client.get(
        f"{_base()}/api/worker/orders/{order_id}/file", headers=_headers()
    )
    if r.status_code == 404:
        return None
    r.raise_for_status()
    suffix = Path(file_name or "input.bin").suffix or ".bin"
    path = dest / f"input{suffix}"
    path.write_bytes(r.content)
    return path


async def upload_result(
    client: httpx.AsyncClient,
    order_id: int,
    result_path: Path,
    filename: str,
    comment: str,
    qc_status: str | None,
    qc_score: int | None,
    qc_report: str | None,
    qc_attempts: int,
) -> dict:
    data = {
        "result_comment": comment,
        "qc_attempts": str(qc_attempts),
    }
    if filename:
        data["result_name"] = filename
    if qc_status:
        data["qc_status"] = qc_status
    if qc_score is not None:
        data["qc_score"] = str(qc_score)
    if qc_report:
        data["qc_report"] = qc_report

    with result_path.open("rb") as f:
        files = {"result_file": (filename or result_path.name, f)}
        r = await client.post(
            f"{_base()}/api/worker/orders/{order_id}/result",
            headers=_headers(),
            data=data,
            files=files,
            timeout=120,
        )
    r.raise_for_status()
    return r.json()


async def report_error(client: httpx.AsyncClient, order_id: int, error: str) -> None:
    r = await client.post(
        f"{_base()}/api/worker/orders/{order_id}/error",
        headers=_headers(),
        data={"error": error[:4000]},
    )
    r.raise_for_status()


async def process_one(client: httpx.AsyncClient, meta: dict) -> None:
    order_id = meta["id"]
    if not await claim(client, order_id):
        return

    log.info("Обрабатываю заказ #%s (%s)", order_id, meta.get("service_title"))
    try:
        with tempfile.TemporaryDirectory(prefix=f"worker_{order_id}_") as tmp:
            tmp_path = Path(tmp)
            input_path = None
            if meta.get("has_file"):
                input_path = await download_file(
                    client, order_id, tmp_path, meta.get("file_name")
                )

            task = detect_task(meta.get("service_title") or "")
            description = meta.get("description") or ""
            chosen, qc_result, attempts = await _generate_with_qc(
                task, description, input_path, tmp_path
            )

            qc_status = None
            qc_score = None
            qc_report = None
            if qc_result is not None:
                qc_status = "passed" if qc_result.passed else "failed"
                qc_score = qc_result.score
                qc_report = json.dumps(qc_result.to_dict(), ensure_ascii=False)

            resp = await upload_result(
                client,
                order_id,
                chosen.path,
                chosen.filename,
                f"{chosen.comment} · воркер Mac",
                qc_status,
                qc_score,
                qc_report,
                attempts,
            )
            log.info(
                "Заказ #%s готов → status=%s qc=%s",
                order_id, resp.get("status"), resp.get("qc_status"),
            )
    except Exception as exc:
        log.exception("Ошибка заказа #%s", order_id)
        try:
            await report_error(client, order_id, str(exc))
        except Exception:
            log.exception("Не удалось отправить ошибку для #%s", order_id)


async def loop() -> None:
    base = _base()
    log.info(
        "Воркер запущен → %s (интервал %s с)",
        base, config.WORKER_POLL_INTERVAL,
    )
    async with httpx.AsyncClient(timeout=60) as client:
        while True:
            try:
                # разбудить Render free-tier
                try:
                    await client.get(base + "/", timeout=30)
                except Exception:
                    pass
                pending = await fetch_pending(client)
                if pending:
                    log.info("В очереди: %s заказ(ов)", len(pending))
                for meta in pending:
                    await process_one(client, meta)
            except httpx.HTTPStatusError as exc:
                log.error("HTTP %s: %s", exc.response.status_code, exc.response.text[:300])
            except Exception:
                log.exception("Ошибка цикла воркера")
            await asyncio.sleep(config.WORKER_POLL_INTERVAL)


def main() -> None:
    try:
        asyncio.run(loop())
    except KeyboardInterrupt:
        log.info("Остановлен")
        sys.exit(0)


if __name__ == "__main__":
    main()
