"""Навык «Поиск файлов»: найти файл или папку по имени.

Ищем только в понятных местах — домашняя папка, рабочий стол, загрузки,
документы, — с ограничением по глубине, времени и числу просмотренных папок.
Иначе «найди файл» на машине с сотнями тысяч файлов превращалось бы в минуты
работы диска. Что искали и сколько нашли — видно в журнале.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from jarvis.skills import fail

#: сколько секунд максимум ищем и сколько папок просматриваем
MAX_SECONDS = 6.0
MAX_DIRS = 4000
MAX_DEPTH = 5
#: столько находок показываем
MAX_RESULTS = 5

#: куда не заглядываем: системные и служебные каталоги
SKIP_DIRS = {
    ".cache", ".git", ".svn", ".hg", ".venv", "venv", ".tox", "node_modules", "__pycache__",
    "AppData", "Application Data", "Local Settings", "$Recycle.Bin", "System Volume Information",
    "Windows", "Program Files", "Program Files (x86)", "ProgramData", "Recovery",
    "site-packages", "dist-packages", "Temp", "temp", ".local", ".npm", ".gradle", ".cargo",
    "snap", ".config", ".mozilla", ".steam", "steamapps",
}


def _roots() -> list[Path]:
    """Где искать: домашняя папка и её понятные подпапки."""
    home = Path.home()
    roots = [home]
    for name in ("Desktop", "Рабочий стол", "Downloads", "Загрузки", "Documents",
                 "Документы", "Pictures", "Изображения", "Videos", "Music"):
        candidate = home / name
        if candidate.is_dir():
            roots.append(candidate)
    return roots


def _match(name: str, query: str) -> bool:
    name = name.lower()
    query = query.lower()
    if name == query:
        return True
    if query in name:
        return True
    # «загрузки.txt» найдёт «Загрузки.txt» и «загрузки (1).txt»
    stem_query = query.rsplit(".", 1)[0]
    return bool(stem_query) and stem_query in name.rsplit(".", 1)[0]


def _search(query: str) -> tuple[list[Path], bool]:
    """Ищет файлы. Возвращает (находки, остановились_ли_по_времени)."""
    started = time.monotonic()
    visited = 0
    results: list[Path] = []
    stack: list[tuple[Path, int]] = [(root, 0) for root in _roots()]
    while stack:
        folder, depth = stack.pop()
        visited += 1
        if visited > MAX_DIRS or time.monotonic() - started > MAX_SECONDS:
            return results, True
        try:
            entries = list(os.scandir(folder))
        except (OSError, PermissionError):
            continue
        for entry in entries:
            try:
                if entry.is_file(follow_symlinks=False) and _match(entry.name, query):
                    results.append(Path(entry.path))
                    if len(results) >= MAX_RESULTS:
                        return results, False
                elif (entry.is_dir(follow_symlinks=False) and depth < MAX_DEPTH
                      and entry.name not in SKIP_DIRS and not entry.name.startswith(".")):
                    stack.append((Path(entry.path), depth + 1))
            except OSError:
                continue
    return results, False


def _find(ctx, intent):
    query = str(intent.args.get(intent.action.capture_field, "")).strip().strip("«»\"'")
    if not query:
        return fail(ctx, "skill.files.empty_query")
    found, stopped = _search(query)
    ctx.log.info("поиск «%s»: найдено %d%s", query, len(found),
                 ", остановился по времени" if stopped else "")
    if not found:
        return fail(ctx, "skill.files.missing", name=query)
    if len(found) == 1:
        return ctx.t("skill.files.found", path=str(found[0]))
    paths = "; ".join(str(item) for item in found[:3])
    answer = ctx.t("skill.files.found_many", paths=paths)
    if stopped:
        answer += " (поиск остановлен по времени — часть папок не проверена)"
    return answer


HANDLERS = {"find": _find}
