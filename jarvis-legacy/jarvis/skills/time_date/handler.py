"""Навык «Время и дата»."""

from __future__ import annotations

from datetime import datetime

WEEKDAYS = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня",
          "июля", "августа", "сентября", "октября", "ноября", "декабря"]


def _time(ctx, intent):
    now = datetime.now()
    return ctx.t("skill.time.now", time=now.strftime("%H:%M"))


def _date(ctx, intent):
    now = datetime.now()
    human = f"{now.day} {MONTHS[now.month - 1]} {now.year} года, {WEEKDAYS[now.weekday()]}"
    return ctx.t("skill.time.date", date=human)


HANDLERS = {"time": _time, "date": _date}
