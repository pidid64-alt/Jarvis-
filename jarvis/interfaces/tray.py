"""Значок в трее — необязательная часть окна.

Трей нужен, чтобы Jarvis не мешал: окно можно закрыть, а ассистент останется
доступен по хоткею. Если библиотека pystray не установлена или рабочий стол её
не поддерживает, окно просто скажет об этом и продолжит работать как обычно —
без трея ничего не ломается.
"""

from __future__ import annotations

from typing import Any, Callable

from ..core.logging_setup import get_logger

log = get_logger("interfaces.tray")

ICON_SIZE = 64


def tray_available() -> tuple[bool, str]:
    """Проверяет, можно ли показать значок в трее (без запуска трея)."""
    try:
        import pystray  # noqa: F401
    except Exception as exc:  # noqa: BLE001 - библиотеки может просто не быть
        return False, f"нет библиотеки pystray ({exc})"
    try:
        from PIL import Image, ImageDraw  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        return False, f"нет библиотеки Pillow ({exc})"
    return True, "готов"


def make_icon_image(color: str = "#2f81f7", background: str = "#101418") -> Any:
    """Рисует простой значок: круг с буквой J. Своих картинок не требуется."""
    from PIL import Image, ImageDraw

    image = Image.new("RGBA", (ICON_SIZE, ICON_SIZE), background)
    draw = ImageDraw.Draw(image)
    margin = 6
    draw.ellipse((margin, margin, ICON_SIZE - margin, ICON_SIZE - margin), fill=color)
    draw.line((ICON_SIZE // 2, 18, ICON_SIZE // 2, 42), fill=background, width=5)
    draw.arc((ICON_SIZE // 2 - 12, 30, ICON_SIZE // 2 + 10, 50), start=0, end=140,
             fill=background, width=5)
    return image


def create_tray(*, title: str, tooltip: str, on_show: Callable[[], None],
                on_listen: Callable[[], None] | None = None,
                on_quit: Callable[[], None] | None = None,
                listen_title: str = "Слушать", quit_title: str = "Выход",
                color: str = "#2f81f7") -> Any | None:
    """Создаёт значок в трее. Возвращает объект с ``run()``/``stop()`` или None.

    Обработчики вызываются в потоке трея — окно само переносит их в главный поток.
    """
    ready, reason = tray_available()
    if not ready:
        log.warning("трей недоступен: %s", reason)
        return None
    try:
        import pystray
    except Exception as exc:  # noqa: BLE001 - библиотека могла не установиться
        log.warning("трей недоступен: %s", exc)
        return None

    items = [pystray.MenuItem(title, lambda *_: on_show(), default=True)]
    if on_listen is not None:
        items.append(pystray.MenuItem(listen_title, lambda *_: on_listen()))
    if on_quit is not None:
        items.append(pystray.MenuItem(quit_title, lambda *_: on_quit()))
    menu = pystray.Menu(*items)
    try:
        icon = pystray.Icon("jarvis", make_icon_image(color), tooltip, menu)
    except Exception:  # noqa: BLE001 - на некоторых столах трей недоступен
        log.exception("не удалось создать значок в трее")
        return None
    return icon
