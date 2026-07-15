"""Парсер Work-Zilla — лента заданий (устроена похоже на YouDo)."""

from .youdo import YoudoSource


class WorkzillaSource(YoudoSource):
    name = "workzilla"
    can_reply = False
    # Публичная витрина задач Work-Zilla
    _base_url = "https://work-zilla.com/orders"
