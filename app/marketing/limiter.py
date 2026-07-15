"""Антиспам-лимитер: не более одного отклика на площадку за интервал."""

import time

from .. import config

_last_reply: dict[str, float] = {}


def can_reply(source: str) -> bool:
    last = _last_reply.get(source, 0.0)
    return (time.monotonic() - last) >= config.MARKETING_MIN_REPLY_INTERVAL


def mark_replied(source: str) -> None:
    _last_reply[source] = time.monotonic()


def seconds_until_allowed(source: str) -> float:
    last = _last_reply.get(source, 0.0)
    remaining = config.MARKETING_MIN_REPLY_INTERVAL - (time.monotonic() - last)
    return max(0.0, remaining)
