"""Внешний вид окна Jarvis — в одном месте.

Правило: **в окне нет ни одного цвета, размера шрифта или отступа, написанного
в коде**. Всё берётся отсюда, поэтому смена темы — правка одного файла, а не
поиск `#2f81f7` по всему окну.

Что внутри:

* ``SPACE`` — шкала отступов 4/8/12/16/24 (шаг 4 и 8, «магия» только здесь);
* ``RADIUS`` — скругления кнопок, полей, карточек, «пузырей» и «таблеток»;
* ``FONTS`` — размеры (заголовок, подзаголовок, основной текст, подпись,
  моноширинный) и предпочтительные семейства шрифтов для Windows и Linux;
* ``PALETTES`` — тёмная и светлая палитры, у каждой роли своё имя;
* ``STATE_COLORS`` — цвета состояний (ожидание, слушаю, думаю, отвечаю,
  спрашиваю подтверждение, ошибка) отдельно для каждой темы;
* ``apply_ttk`` — единственное место, где настраиваются стили ttk;
* ``system_theme`` — какая тема сейчас в системе (Windows/Linux/macOS);
* ``contrast`` — проверка контраста по WCAG, её использует
  ``tools/contrast_check.py`` и тест ``tests/test_theme.py``.

Модуль намеренно **не импортирует tkinter**: его читают и инструменты
документации, и тесты, которым настоящее окно не нужно.

Контраст: весь текст — не ниже 4.5:1 к своему фону, состояния-точки — не ниже
3:1 (это требование WCAG для графических элементов). Значения проверены
тестом, поэтому палитру нельзя испортить случайной правкой.
"""

from __future__ import annotations

import os
import subprocess
import sys
from typing import Any, Iterable, Mapping

# --------------------------------------------------------------------- шкалы

#: единая шкала отступов: 4 / 8 / 12 / 16 / 24
SPACE: dict[str, int] = {"xs": 4, "s": 8, "m": 12, "l": 16, "xl": 24}

#: скругления: у «таблетки» — половина высоты, поэтому большое число
RADIUS: dict[str, int] = {"button": 6, "field": 6, "card": 8, "bubble": 10, "pill": 999}

#: размеры шрифта в пунктах: заголовок, подзаголовок, основной, подпись, моно
FONTS: dict[str, dict[str, Any]] = {
    "title": {"size": 21, "weight": "bold"},
    "heading": {"size": 17, "weight": "bold"},
    "body": {"size": 15},
    "caption": {"size": 13},
    "mono": {"size": 14, "family": "mono"},
}

#: предпочтительные семейства: сначала системные (Windows), затем свободные (Linux)
FAMILIES: dict[str, tuple[str, ...]] = {
    "sans": (
        "Segoe UI Variable Text",   # Windows 11
        "Segoe UI",                 # Windows 10
        "Noto Sans",                # Linux, SIL OFL
        "DejaVu Sans",              # Linux, свободная лицензия
        "Liberation Sans",
        "TkDefaultFont",            # последний шанс — что даёт сам Tk
    ),
    "mono": (
        "Cascadia Mono",            # Windows 11 (MIT)
        "Consolas",                 # Microsoft, входит в Windows
        "DejaVu Sans Mono",         # Linux, свободная лицензия
        "Liberation Mono",
        "Noto Sans Mono",
        "TkFixedFont",
    ),
}

# -------------------------------------------------------------------- палитры

#: тёмная тема — графит, как в приложениях Windows 11
DARK: dict[str, str] = {
    "bg": "#1b1b1f",            # фон окна
    "surface": "#232329",       # карточки, шапка, подвал
    "surface2": "#2c2c33",      # поля, вторичные кнопки, «таблетки»
    "border": "#3a3a42",        # декоративные границы (карточки, разделители)
    "field_border": "#757581",  # граница полей ввода — её видно
    "text": "#f3f3f7",          # основной текст
    "muted": "#a9a9b6",         # подписи, второстепенный текст
    "accent": "#60cdff",        # акцент
    "on_accent": "#10131a",     # текст на акценте
    "input_bg": "#2c2c33",      # поле ввода
    "user_bg": "#60cdff",       # плашка пользователя
    "user_text": "#10131a",
    "assistant_bg": "#232329",  # плашка ассистента
    "assistant_text": "#f3f3f7",
    "ok": "#6ccb5f",
    "warn": "#fce100",
    "err": "#ff99a4",
    "chip_bg": "#2c2c33",
    "chip_text": "#a9a9b6",
    # совместимость с прежними именами (их ещё спрашивают тесты и инструменты)
    "panel": "#232329",
    "entry": "#2c2c33",
    "user": "#60cdff",
    "jarvis": "#f3f3f7",
    "scroll_trough": "#1b1b1f",
    "scroll_thumb": "#4a4a52",
}

#: светлая тема
LIGHT: dict[str, str] = {
    "bg": "#f3f3f3",
    "surface": "#ffffff",
    "surface2": "#f9f9f9",
    "border": "#dcdce1",
    "field_border": "#8f8f95",
    "text": "#1b1b1f",
    "muted": "#5f5f6a",
    "accent": "#0f6cbd",
    "on_accent": "#ffffff",
    "input_bg": "#ffffff",
    "user_bg": "#0f6cbd",
    "user_text": "#ffffff",
    "assistant_bg": "#ffffff",
    "assistant_text": "#1b1b1f",
    "ok": "#0f7b0f",
    "warn": "#9d5d00",
    "err": "#c42b1c",
    "chip_bg": "#ededf0",
    "chip_text": "#5f5f6a",
    "panel": "#ffffff",
    "entry": "#ffffff",
    "user": "#0f6cbd",
    "jarvis": "#1b1b1f",
    "scroll_trough": "#f3f3f3",
    "scroll_thumb": "#c8c8cc",
}

PALETTES: dict[str, dict[str, str]] = {"dark": DARK, "light": LIGHT}

#: состояния окна: цвет точки (и подписи, если понадобится)
STATE_COLORS: dict[str, dict[str, str]] = {
    "dark": {
        "idle": "#a9a9b6",
        "listening": "#6ccb5f",
        "thinking": "#fce100",
        "speaking": "#60cdff",
        "waiting_confirmation": "#f0a35e",
        "error": "#ff99a4",
    },
    "light": {
        "idle": "#5f5f6a",
        "listening": "#0f7b0f",
        "thinking": "#9d5d00",
        "speaking": "#0f6cbd",
        "waiting_confirmation": "#b45309",
        "error": "#c42b1c",
    },
}

#: пороги контраста (WCAG AA): текст 4.5:1, состояния и фокус 3:1.
#: Декоративные границы (карточки, разделители) обязаны лишь различаться —
#: по WCAG 1.4.11 порог 3:1 применяется к элементам, без границы неразличимым;
#: у нас карточка видна по фону, поэтому к ней требование не применяется.
TEXT_CONTRAST_MIN = 4.5
GRAPHIC_CONTRAST_MIN = 3.0
BORDER_CONTRAST_MIN = 1.2

DEFAULT_THEME = "dark"
SYSTEM_THEME = "system"
THEME_CHOICES = (SYSTEM_THEME, "dark", "light")

#: переменная окружения для проверок и любителей фиксированной темы
THEME_ENV = "JARVIS_THEME"

# ------------------------------------------------------------------ тема, палитра


def _clean(value: Any) -> str:
    return str(value or "").strip().lower()


def current_os() -> str:
    """Короткое имя системы: windows, macos, linux (или другое)."""
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


def system_theme(*, probe: bool = True) -> str:
    """Какая тема сейчас в системе: ``dark`` или ``light``.

    Читаем настройки системы без сторонних библиотек:

    * Windows — реестр (``AppsUseLightTheme``);
    * macOS — ``defaults read -g AppleInterfaceStyle``;
    * Linux — ``gsettings`` (GNOME) и переменная ``GTK_THEME``;
    * переменная ``JARVIS_THEME`` перекрывает всё (удобно для проверок).

    Если узнать не удалось, считаем тёмной: это тема по умолчанию у Jarvis.
    """
    override = _clean(os.environ.get(THEME_ENV))
    if override in ("dark", "light"):
        return override
    if not probe:
        return DEFAULT_THEME
    try:
        found = {"windows": _windows_theme, "macos": _macos_theme,
                 "linux": _linux_theme}.get(current_os(), lambda: None)()
    except Exception:  # noqa: BLE001 - тема не повод падать
        found = None
    return found if found in ("dark", "light") else DEFAULT_THEME


def _windows_theme() -> str | None:
    import winreg

    key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                         r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize")
    try:
        value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
    finally:
        winreg.CloseKey(key)
    return "light" if int(value) else "dark"


def _macos_theme() -> str | None:
    result = subprocess.run(["defaults", "read", "-g", "AppleInterfaceStyle"],
                            capture_output=True, text=True, timeout=2)
    return "dark" if "dark" in result.stdout.strip().lower() else "light"


def _linux_theme() -> str | None:
    for command in (["gsettings", "get", "org.gnome.desktop.interface", "color-scheme"],
                    ["gsettings", "get", "org.gnome.desktop.interface", "gtk-theme"],
                    ["kreadconfig5", "--group", "General", "--key", "ColorScheme"]):
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=2)
        except (OSError, subprocess.SubprocessError):
            continue
        answer = result.stdout.strip().strip("'").lower()
        if not answer:
            continue
        if "dark" in answer:
            return "dark"
        if "light" in answer or "default" in answer:
            return "light"
    gtk = _clean(os.environ.get("GTK_THEME"))
    if gtk:
        return "dark" if "dark" in gtk else "light"
    return None


def resolve(name: Any) -> str:
    """Превратить настройку темы (в том числе ``system``) в ``dark`` или ``light``."""
    wanted = _clean(name)
    if wanted == SYSTEM_THEME:
        return system_theme()
    return wanted if wanted in PALETTES else DEFAULT_THEME


def palette(name: Any) -> dict[str, str]:
    """Палитра для темы: ``dark`` или ``light`` (неизвестное — тёмная)."""
    return PALETTES[resolve(name)] if _clean(name) != SYSTEM_THEME else PALETTES[system_theme()]


def state_color(state: Any, theme: Any = DEFAULT_THEME) -> str:
    """Цвет состояния окна (``listening``, ``thinking``, …)."""
    colors = STATE_COLORS[resolve(theme)]
    return colors.get(_clean(state) or "idle", colors["idle"])


# --------------------------------------------------------------------- шрифты


def resolve_families(available: Iterable[str] | None = None) -> dict[str, str]:
    """Выбрать семейства шрифтов из тех, что есть в системе.

    ``available`` — список имён семейств (``tkinter.font.families()``). Сравнение
    без учёта регистра; если ничего не подошло, остаётся то, что даёт сам Tk.
    """
    names = {str(item).strip().lower(): str(item) for item in (available or [])}
    chosen: dict[str, str] = {}
    for kind, preferences in FAMILIES.items():
        for candidate in preferences:
            if candidate.startswith("Tk"):
                chosen[kind] = candidate      # «последний шанс» — имя стиля Tk
                break
            if candidate.lower() in names:
                chosen[kind] = names[candidate.lower()]
                break
        else:
            chosen[kind] = preferences[-1]
    return chosen


def font_spec(kind: str, families: Mapping[str, str] | None = None) -> tuple:
    """Кортеж для Tk: ``(семейство, размер, начертание)``.

    Пример: ``font=theme.font_spec("heading", self.fonts)``.
    """
    spec = FONTS.get(kind) or FONTS["body"]
    families = families or {}
    family_kind = spec.get("family", "sans")
    family = families.get(family_kind) or families.get("sans") or "TkDefaultFont"
    result: list[Any] = [family, spec["size"]]
    if spec.get("weight"):
        result.append(spec["weight"])
    return tuple(result)


# ------------------------------------------------------------------ стили ttk


def ttk_style_config(palette_: Mapping[str, str]) -> dict[str, dict[str, Any]]:
    """Что настроить в ttk. Отдельная функция — её проверяет тест."""
    space, radius = SPACE, RADIUS
    return {
        "TFrame": {"background": palette_["bg"]},
        "TNotebook": {"background": palette_["bg"], "borderwidth": 0, "tabmargins": (space["m"], space["xs"], 0, 0)},
        "TNotebook.Tab": {
            "padding": (space["l"], space["s"]),
            "background": palette_["bg"],
            "foreground": palette_["muted"],
            "borderwidth": 0,
        },
        "TLabel": {"background": palette_["bg"], "foreground": palette_["text"]},
        "Muted.TLabel": {"background": palette_["bg"], "foreground": palette_["muted"]},
        "Heading.TLabel": {"background": palette_["bg"], "foreground": palette_["text"]},
        "TButton": {"padding": (space["m"], space["s"]), "borderwidth": 0, "relief": "flat",
                    "background": palette_["surface2"], "foreground": palette_["text"]},
        "TEntry": {"fieldbackground": palette_["input_bg"], "foreground": palette_["text"],
                   "bordercolor": palette_["border"], "lightcolor": palette_["border"],
                   "darkcolor": palette_["border"], "padding": (space["s"], space["xs"])},
        "TCombobox": {"fieldbackground": palette_["input_bg"], "foreground": palette_["text"],
                      "background": palette_["surface2"], "arrowcolor": palette_["muted"],
                      "bordercolor": palette_["border"]},
        "TCheckbutton": {"background": palette_["bg"], "foreground": palette_["text"],
                         "focuscolor": palette_["accent"]},
        "Treeview": {"background": palette_["surface"], "fieldbackground": palette_["surface"],
                     "foreground": palette_["text"], "bordercolor": palette_["border"],
                     "rowheight": FONTS["body"]["size"] + space["l"]},
        "Treeview.Heading": {"background": palette_["surface"], "foreground": palette_["muted"],
                             "relief": "flat", "padding": (space["s"], space["s"])},
        "TScrollbar": {"background": palette_["surface"], "troughcolor": palette_["scroll_trough"],
                       "bordercolor": palette_["scroll_trough"], "arrowcolor": palette_["muted"],
                       "width": SPACE["m"]},
        "Vertical.TScrollbar": {"background": palette_["scroll_thumb"],
                                "troughcolor": palette_["scroll_trough"],
                                "bordercolor": palette_["scroll_trough"],
                                "arrowcolor": palette_["muted"]},
        "TSeparator": {"background": palette_["border"]},
    }


def ttk_style_map(palette_: Mapping[str, str]) -> dict[str, dict[str, Any]]:
    """Состояния виджетов: наведение, нажатие, недоступность, фокус."""
    return {
        "TNotebook.Tab": {
            "background": [("selected", palette_["surface2"]), ("active", palette_["surface"])],
            "foreground": [("selected", palette_["text"]), ("active", palette_["text"])],
        },
        "TButton": {
            "background": [("disabled", palette_["bg"]), ("pressed", palette_["surface"]),
                           ("active", palette_["chip_bg"])],
            "foreground": [("disabled", palette_["muted"])],
        },
        "TCombobox": {
            "fieldbackground": [("readonly", palette_["input_bg"])],
            "foreground": [("disabled", palette_["muted"])],
        },
        "TCheckbutton": {
            "background": [("active", palette_["bg"])],
            "foreground": [("disabled", palette_["muted"])],
        },
        "Treeview": {"background": [("selected", palette_["accent"])],
                     "foreground": [("selected", palette_["on_accent"])]},
        "TEntry": {"fieldbackground": [("disabled", palette_["bg"])],
                   "foreground": [("disabled", palette_["muted"])]},
    }


def apply_ttk(style: Any, palette_: Mapping[str, str], theme_name: str = DEFAULT_THEME) -> None:
    """Настроить ttk-стили по палитре. Единственное место с ttk-настройками.

    Тема ttk ``clam`` берётся потому, что её можно перекрасить целиком; на
    системных темах (``vista`` на Windows) цвета задать нельзя.
    """
    try:
        style.theme_use("clam")
    except Exception:  # noqa: BLE001 - тема может отсутствовать в урезанной сборке
        pass
    colors = STATE_COLORS[resolve(theme_name)]
    for name, options in ttk_style_config(palette_).items():
        try:
            style.configure(name, **options)
        except Exception:  # noqa: BLE001 - не все опции есть во всех версиях Tk
            pass
    for name, options in ttk_style_map(palette_).items():
        try:
            style.map(name, **options)
        except Exception:  # noqa: BLE001
            pass
    for name, options in {"Accent.TButton": {"background": palette_["accent"],
                                             "foreground": palette_["on_accent"]},
                          "Danger.TButton": {"background": colors["error"],
                                             "foreground": palette_["on_accent"]},
                          "Card.TFrame": {"background": palette_["surface"]},
                          "Card.TLabel": {"background": palette_["surface"], "foreground": palette_["text"]},
                          "CardMuted.TLabel": {"background": palette_["surface"],
                                               "foreground": palette_["muted"]}}.items():
        try:
            style.configure(name, **options)
        except Exception:  # noqa: BLE001
            pass
    try:
        style.map("Accent.TButton",
                  background=[("disabled", palette_["chip_bg"]), ("pressed", palette_["surface2"]),
                              ("active", palette_["accent"])],
                  foreground=[("disabled", palette_["muted"])])
    except Exception:  # noqa: BLE001
        pass


def enable_dpi_awareness() -> str:
    """Сказать Windows, что мы сами умеем масштаб 125/150 %.

    Без этого система растягивает картинку и текст blur-ом. Возвращает, что
    именно удалось сделать (для журнала), на других системах — «не требуется».
    """
    if current_os() != "windows":
        return "не требуется"
    try:
        import ctypes

        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)  # PROCESS_SYSTEM_DPI_AWARE
            return "shcore: системный DPI"
        except Exception:  # noqa: BLE001 - старые версии Windows
            ctypes.windll.user32.SetProcessDPIAware()
            return "user32: DPI-aware"
    except Exception as exc:  # noqa: BLE001
        return f"не удалось: {exc}"


def tk_scaling(root: Any) -> float:
    """Подобрать масштаб Tk под систему (125 %, 150 % и т. д.).

    Точка отсчёта — 96 dpi: при 96 dpi коэффициент 1.0. Возвращает применённый
    коэффициент, чтобы это можно было записать в журнал и проверить тестом.
    """
    try:
        screen_pixels_per_inch = float(root.winfo_fpixels("1i"))
    except Exception:  # noqa: BLE001
        return float(root.tk.call("tk", "scaling")) if hasattr(root, "tk") else 1.0
    points_per_pixel = 72.0 / screen_pixels_per_inch
    try:
        root.tk.call("tk", "scaling", points_per_pixel)
    except Exception:  # noqa: BLE001
        pass
    return points_per_pixel


# -------------------------------------------------------------------- контраст


def _channel(value: int) -> float:
    srgb = value / 255
    return srgb / 12.92 if srgb <= 0.03928 else ((srgb + 0.055) / 1.055) ** 2.4


def luminance(color: str) -> float:
    """Относительная яркость цвета ``#rrggbb``."""
    text = str(color).strip().lstrip("#")
    red, green, blue = (int(text[index:index + 2], 16) for index in (0, 2, 4))
    return 0.2126 * _channel(red) + 0.7152 * _channel(green) + 0.0722 * _channel(blue)


def contrast(foreground: str, background: str) -> float:
    """Отношение контраста двух цветов: от 1.0 до 21.0."""
    first, second = luminance(foreground), luminance(background)
    lighter, darker = max(first, second), min(first, second)
    return round((lighter + 0.05) / (darker + 0.05), 2)


#: пары «текст на фоне», которые обязаны проходить порог 4.5:1
TEXT_PAIRS: tuple[tuple[str, str, str], ...] = (
    ("основной текст на фоне окна", "text", "bg"),
    ("основной текст на панели", "text", "surface"),
    ("подпись на фоне окна", "muted", "bg"),
    ("подпись на панели", "muted", "surface"),
    ("текст в поле ввода", "text", "input_bg"),
    ("подпись в поле ввода", "muted", "input_bg"),
    ("текст на акценте", "on_accent", "accent"),
    ("акцент как текст", "accent", "bg"),
    ("акцент как текст на панели", "accent", "surface"),
    ("плашка пользователя", "user_text", "user_bg"),
    ("плашка ассистента", "assistant_text", "assistant_bg"),
    ("подпись на «таблетке»", "chip_text", "chip_bg"),
)

#: пары «графический элемент на фоне», порог 3:1: границы полей и акцент
GRAPHIC_PAIRS: tuple[tuple[str, str, str], ...] = (
    ("акцент (наведение, фокус) на фоне", "accent", "bg"),
    ("акцент (наведение, фокус) на панели", "accent", "surface"),
    ("граница поля ввода на панели", "field_border", "surface"),
    ("граница поля ввода на самом поле", "field_border", "input_bg"),
)

#: декоративные границы: должны быть различимы, но 3:1 к ним не применяется
BORDER_PAIRS: tuple[tuple[str, str, str], ...] = (
    ("декоративная граница на фоне", "border", "bg"),
    ("декоративная граница на панели", "border", "surface"),
)


def audit(name: Any = DEFAULT_THEME) -> list[tuple[str, float, float, bool]]:
    """Проверить палитру: (что проверяли, контраст, порог, прошло ли)."""
    colors = palette(name)
    rows: list[tuple[str, float, float, bool]] = []
    for title, foreground, background in TEXT_PAIRS:
        ratio = contrast(colors[foreground], colors[background])
        rows.append((f"{title} ({colors[foreground]} на {colors[background]})",
                     ratio, TEXT_CONTRAST_MIN, ratio >= TEXT_CONTRAST_MIN))
    for title, foreground, background in GRAPHIC_PAIRS:
        ratio = contrast(colors[foreground], colors[background])
        rows.append((f"{title} ({colors[foreground]} на {colors[background]})",
                     ratio, GRAPHIC_CONTRAST_MIN, ratio >= GRAPHIC_CONTRAST_MIN))
    for title, foreground, background in BORDER_PAIRS:
        ratio = contrast(colors[foreground], colors[background])
        rows.append((f"{title} ({colors[foreground]} на {colors[background]})",
                     ratio, BORDER_CONTRAST_MIN, ratio >= BORDER_CONTRAST_MIN))
    theme_name = resolve(name)
    for state, color in STATE_COLORS[theme_name].items():
        for background_key in ("surface", "bg"):
            ratio = contrast(color, colors[background_key])
            rows.append((f"состояние «{state}» ({color} на {colors[background_key]})",
                         ratio, GRAPHIC_CONTRAST_MIN, ratio >= GRAPHIC_CONTRAST_MIN))
    return rows


def describe() -> str:
    """Короткая справка о теме — её показывает `jarvis config`."""
    lines = [f"тема: {DEFAULT_THEME} по умолчанию, есть {', '.join(THEME_CHOICES)}",
             f"шкала отступов: {', '.join(str(value) for value in SPACE.values())}",
             f"размеры шрифта: " + ", ".join(f"{key} {value['size']}" for key, value in FONTS.items())]
    for name in PALETTES:
        bad = [row for row in audit(name) if not row[3]]
        lines.append(f"палитра «{name}»: пар ниже порога — {len(bad)}")
    return "\n".join(lines)
