#!/usr/bin/env python3
"""Макеты интерфейса Jarvis: «как сейчас» и предложенные направления стиля.

Инструмент только для разработки и документации. Он рисует картинки (Pillow),
чтобы обсуждать внешний вид до правки кода: настоящее окно требует Tkinter и
графической среды, а показать варианты нужно и в песочнице, и в переписке.

Два режима:

* ``before`` — как выглядело окно на Tkinter (оставлено для сравнения в отчёте
  по стилю): светлые системные виджеты в тёмном окне, классическая прокрутка,
  наложение подписей в подвале;
* ``fluent`` / ``neon`` / ``calm`` — три направления нового стиля. Палитры,
  шрифты, радиусы и отступы лежат в ``STYLES`` — это те же токены, которые
  потом попадут в ``jarvis/interfaces/theme.py``.

Запуск (нужен Pillow)::

    python tools/style_preview.py --style before --page chat --theme dark
    python tools/style_preview.py --style fluent --page skills --theme light
    python tools/style_preview.py --style all --theme both --out-dir docs/style
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from jarvis.interfaces import theme as window_theme  # noqa: E402

WIDTH, HEIGHT = 1040, 700
SIDEBAR_W = 216
HEADER_H = 58
NAV_H = 44
INPUT_H = 44
FOOTER_H = 26
MARGIN = 16

# --------------------------------------------------------------------- шрифты

FONT_FILES = {
    "sans": [
        "C:/Windows/Fonts/SegoeUIVF.ttf",     # Windows 11: Segoe UI Variable
        "C:/Windows/Fonts/segoeui.ttf",       # Windows 10: Segoe UI
        "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ],
    "sans_bold": [
        "C:/Windows/Fonts/SegoeUIVF.ttf",
        "C:/Windows/Fonts/segoeuib.ttf",
        "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ],
    "mono": [
        "C:/Windows/Fonts/CascadiaMono.ttf",
        "C:/Windows/Fonts/consola.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    ],
}

_font_cache: dict[tuple[str, int], object] = {}


def font(kind: str, size: int):
    from PIL import ImageFont

    key = (kind, size)
    if key in _font_cache:
        return _font_cache[key]
    for path in FONT_FILES[kind]:
        if Path(path).exists():
            chosen = ImageFont.truetype(path, size)
            break
    else:
        chosen = ImageFont.load_default()
    _font_cache[key] = chosen
    return chosen


# ---------------------------------------------------------------------- цвета


def mix(first: str, second: str, amount: float) -> str:
    """Смешать два цвета: amount=0 → первый, amount=1 → второй."""
    first, second = first.lstrip("#"), second.lstrip("#")
    parts = []
    for index in (0, 2, 4):
        left = int(first[index:index + 2], 16)
        right = int(second[index:index + 2], 16)
        parts.append(round(left + (right - left) * amount))
    return "#%02x%02x%02x" % tuple(parts)


def translucent(bg: str, color: str, amount: float) -> str:
    """Полупрозрачный слой поверх фона — имитация альфы смешиванием."""
    return mix(bg, color, amount)


# ------------------------------------------------------------------- токены

#: шкалы берём из темы окна, чтобы макет не мог разойтись с кодом
SPACE = dict(window_theme.SPACE)
FONTS = {name: spec["size"] for name, spec in window_theme.FONTS.items()}


def window_palette(name: str) -> dict[str, str]:
    """Палитра принятого направления — прямо из темы окна.

    Два ключа макету нужны свои: ``user_border``/``assistant_border`` — рамки
    «пузырей» чата (в теме они берутся из акцента и границы).
    """
    palette = dict(window_theme.PALETTES[name])
    palette["user_border"] = palette["accent"]
    palette["assistant_border"] = palette["border"]
    return palette

STYLES: dict[str, dict] = {
    "before": {
        "title": "Как сейчас (Этап 2)",
        "note": "ttk «clam», светлые виджеты в тёмном окне, классическая прокрутка",
        "kind": "classic",
        "nav": "classic",
        "radius": {"card": 0, "button": 2, "field": 1, "chip": 0, "bubble": 0},
        "shadow": False,
        "palette": {
            "dark": {
                "bg": "#101418", "surface": "#161b22", "surface2": "#0d1117",
                "border": "#8b949e", "text": "#e6edf3", "muted": "#8b949e",
                "accent": "#2f81f7", "on_accent": "#ffffff", "input_bg": "#0d1117",
                "user_text": "#7ee787", "assistant_text": "#79c0ff",
                "user_bg": "#0d1117", "assistant_bg": "#0d1117",
                "chip_bg": "#161b22", "chip_text": "#8b949e",
                "ok": "#3fb950", "warn": "#d29922", "err": "#f85149",
                "widget": "#d9d9d9", "widget_text": "#000000",
                "scroll_trough": "#d9d9d9", "scroll_thumb": "#a3a3a3",
            },
            "light": {
                "bg": "#f6f8fa", "surface": "#ffffff", "surface2": "#e6e6e6",
                "border": "#8b949e", "text": "#1f2328", "muted": "#59636e",
                "accent": "#0969da", "on_accent": "#ffffff", "input_bg": "#ffffff",
                "user_text": "#1a7f37", "assistant_text": "#0a3069",
                "user_bg": "#ffffff", "assistant_bg": "#ffffff",
                "chip_bg": "#ffffff", "chip_text": "#59636e",
                "ok": "#1a7f37", "warn": "#9a6700", "err": "#cf222e",
                "widget": "#e3e3e3", "widget_text": "#1f2328",
                "scroll_trough": "#e6e6e6", "scroll_thumb": "#b4b4b4",
            },
        },
    },
    "fluent": {
        "title": "Направление A — «Fluent 11»",
        "note": "как приложения Windows 11: мягкие плашки, один акцент, аккуратные отступы",
        "kind": "modern",
        "nav": "segmented",
        "radius": {"card": 8, "button": 6, "field": 6, "chip": 999, "bubble": 10},
        "shadow": True,
        "bubbles": {"user": "accent", "assistant": "card"},
        "palette": {"dark": window_palette("dark"), "light": window_palette("light")},
    },
    "neon": {
        "title": "Направление B — «Тёмный футуризм»",
        "note": "глубокий графит, бирюзовый акцент, свечение у состояния, моноширинный журнал",
        "kind": "modern",
        "nav": "underline",
        "radius": {"card": 12, "button": 8, "field": 8, "chip": 999, "bubble": 12},
        "shadow": True,
        "glow": True,
        "bubbles": {"user": "outline", "assistant": "bar"},
        "palette": {
            "dark": {
                "bg": "#0b0f14", "surface": "#111823", "surface2": "#16202c",
                "border": "#1f2a37", "text": "#e6f1ff", "muted": "#8296b0",
                "accent": "#22d3ee", "on_accent": "#04222a", "input_bg": "#111823",
                "user_text": "#cfe9f5", "assistant_text": "#e6f1ff",
                "user_bg": "#16202c", "assistant_bg": "#111823",
                "user_border": "#22d3ee", "assistant_border": "#1f2a37",
                "chip_bg": "#16202c", "chip_text": "#8296b0",
                "ok": "#34d399", "warn": "#fbbf24", "err": "#f87171",
                "scroll_trough": "#0b0f14", "scroll_thumb": "#203040",
            },
            "light": {
                "bg": "#f4f7fa", "surface": "#ffffff", "surface2": "#eef3f8",
                "border": "#dce5ef", "text": "#0f172a", "muted": "#5a6b80",
                "accent": "#0e7490", "on_accent": "#ffffff", "input_bg": "#ffffff",
                "user_text": "#0f172a", "assistant_text": "#0f172a",
                "user_bg": "#eef3f8", "assistant_bg": "#ffffff",
                "user_border": "#0e7490", "assistant_border": "#dce5ef",
                "chip_bg": "#eef3f8", "chip_text": "#5a6b80",
                "ok": "#059669", "warn": "#b45309", "err": "#dc2626",
                "scroll_trough": "#f4f7fa", "scroll_thumb": "#c3d0dd",
            },
        },
    },
    "calm": {
        "title": "Направление C — «Спокойный минимализм»",
        "note": "боковое меню, много воздуха, сообщения без плашек, спокойный синий",
        "kind": "modern",
        "nav": "sidebar",
        "radius": {"card": 8, "button": 6, "field": 6, "chip": 999, "bubble": 6},
        "shadow": False,
        "bubbles": {"user": "plain", "assistant": "plain"},
        "palette": {
            "dark": {
                "bg": "#171819", "surface": "#1f2124", "surface2": "#24272b",
                "border": "#2a2d31", "text": "#ecedee", "muted": "#9aa0a6",
                "accent": "#8ab4f8", "on_accent": "#0e1626", "input_bg": "#1f2124",
                "user_text": "#ecedee", "assistant_text": "#ecedee",
                "user_bg": "#24272b", "assistant_bg": "#171819",
                "user_border": "#2a2d31", "assistant_border": "#2a2d31",
                "chip_bg": "#24272b", "chip_text": "#9aa0a6",
                "ok": "#81c995", "warn": "#fdd663", "err": "#f28b82",
                "scroll_trough": "#171819", "scroll_thumb": "#3a3d41",
            },
            "light": {
                "bg": "#fafaf9", "surface": "#ffffff", "surface2": "#f4f4f2",
                "border": "#eaeaea", "text": "#1f1f1f", "muted": "#6b6b6b",
                "accent": "#1d4ed8", "on_accent": "#ffffff", "input_bg": "#ffffff",
                "user_text": "#1f1f1f", "assistant_text": "#1f1f1f",
                "user_bg": "#f4f4f2", "assistant_bg": "#fafaf9",
                "user_border": "#eaeaea", "assistant_border": "#eaeaea",
                "chip_bg": "#f4f4f2", "chip_text": "#6b6b6b",
                "ok": "#0f7b0f", "warn": "#9d5d00", "err": "#c42b1c",
                "scroll_trough": "#fafaf9", "scroll_thumb": "#d2d2d0",
            },
        },
    },
}

PAGES = [
    ("chat", "Главная"),
    ("skills", "Навыки"),
    ("settings", "Настройки"),
    ("journal", "Журнал"),
    ("about", "О программе"),
]

MESSAGES = [
    ("jarvis", "Здравствуйте. Напишите вопрос или нажмите «Слушать»."),
    ("user", "сколько времени"),
    ("jarvis", "Сейчас 19:48."),
    ("user", "открой браузер"),
    ("jarvis", "Готово."),
]


# ------------------------------------------------------------------ рисование


class Paint:
    """Мелкие операции рисования: плашки, «таблетки», текст, иконки."""

    def __init__(self, draw, style: dict, palette: dict[str, str]):
        self.d = draw
        self.style = style
        self.p = palette
        self.r = style["radius"]
        self.modern = style.get("kind", "modern") == "modern"

    @property
    def dark(self) -> bool:
        text = self.p["text"].lstrip("#")
        red, green, blue = (int(text[i:i + 2], 16) for i in (0, 2, 4))
        return (red + green + blue) / 3 > 150

    def rect(self, box, fill=None, outline=None, width=1):
        self.d.rectangle(box, fill=fill, outline=outline, width=width)

    def rr(self, box, radius=None, fill=None, outline=None, width=1):
        radius = self.r["card"] if radius is None else radius
        if radius <= 0:
            self.rect(box, fill=fill, outline=outline, width=width)
        else:
            self.d.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)

    def shadow(self, box, radius=None):
        if not self.style.get("shadow"):
            return
        radius = self.r["card"] if radius is None else radius
        shade = translucent(self.p["bg"], "#000000" if self.dark else "#8a8a8a",
                            0.26 if self.dark else 0.16)
        self.d.rounded_rectangle((box[0] + 1, box[1] + 2, box[2] + 1, box[3] + 3),
                                 radius=radius, fill=shade)

    def glow(self, cx, cy, color, *, radius=10):
        if not self.style.get("glow"):
            return
        for step, alpha in ((radius, 0.16), (radius - 3, 0.28)):
            self.d.ellipse((cx - step, cy - step, cx + step, cy + step),
                           fill=translucent(self.p["surface"], color, alpha))

    # --- текст
    def text(self, xy, string, size=None, kind="sans", fill=None, anchor="la"):
        size = FONTS["body"] if size is None else size
        self.d.text(xy, string, font=font(kind, size), fill=fill or self.p["text"], anchor=anchor)

    def width(self, string, size=None, kind="sans") -> int:
        size = FONTS["body"] if size is None else size
        return int(self.d.textlength(string, font=font(kind, size)))

    # --- составные элементы
    def chip(self, x, y, label, *, fg=None, bg=None, pad=10, height=24, radius=None,
             dot=None, outline=None):
        fg = fg or self.p["chip_text"]
        bg = bg or self.p["chip_bg"]
        radius = min(self.r["chip"] if radius is None else radius, height // 2)
        width = self.width(label, FONTS["caption"]) + pad * 2 + (12 if dot else 0)
        self.rr((x, y, x + width, y + height), radius, fill=bg, outline=outline)
        text_x = x + pad
        if dot:
            self.d.ellipse((x + pad, y + height / 2 - 3, x + pad + 6, y + height / 2 + 3), fill=dot)
            text_x += 12
        self.text((text_x, y + height / 2 + 1), label, FONTS["caption"], fill=fg, anchor="lm")
        return x + width

    def button(self, x, y, label, *, primary=False, height=34, icon=None, radius=None,
               width=None, disabled=False, ghost=False, text=None):
        radius = self.r["button"] if radius is None else radius
        if primary:
            fill, fg = self.p["accent"], self.p["on_accent"]
        elif ghost:
            fill, fg = None, self.p["accent"]
        elif disabled:
            fill, fg = translucent(self.p["bg"], self.p["muted"], 0.14), self.p["muted"]
        else:
            fill, fg = self.p["surface2"], self.p["text"]
        pad = SPACE["l"]
        text = label if text is None else text
        text_w = self.width(text, FONTS["body"]) if text else 0
        icon_w = 20 if icon else 0
        box_w = width or (pad * 2 + icon_w + text_w)
        self.rr((x, y, x + box_w, y + height), radius, fill=fill)
        inner = x + (box_w - icon_w - text_w) / 2
        if icon:
            icon(self, inner + 8, y + height / 2, fg)
            inner += icon_w
        if text:
            self.text((inner, y + height / 2 + 1), text, FONTS["body"], fill=fg, anchor="lm")
        return x + box_w

    def field(self, box, text, *, placeholder=False, focus=False, mono=False, radius=None,
              trailing=None):
        radius = self.r["field"] if radius is None else radius
        border = self.p["accent"] if focus else self.p["border"]
        self.rr(box, radius, fill=self.p["input_bg"], outline=border, width=2 if focus else 1)
        color = self.p["muted"] if placeholder else self.p["text"]
        kind = "mono" if mono else "sans"
        size = FONTS["mono"] if mono else FONTS["body"]
        self.text((box[0] + SPACE["m"], (box[1] + box[3]) / 2 + 1), text, size, kind,
                  fill=color, anchor="lm")
        if trailing:
            trailing(self, box)

    def toggle(self, x, y, *, on_=True, width=40, height=22):
        track = self.p["accent"] if on_ else translucent(self.p["surface"], self.p["muted"], 0.45)
        self.rr((x, y, x + width, y + height), height // 2, fill=track)
        knob_r = height / 2 - 3
        center = x + width - height / 2 if on_ else x + height / 2
        self.d.ellipse((center - knob_r, y + height / 2 - knob_r, center + knob_r, y + height / 2 + knob_r),
                       fill="#ffffff" if on_ else self.p["muted"])

    def scrollbar(self, x, top, bottom):
        trough, thumb = self.p["scroll_trough"], self.p["scroll_thumb"]
        if not self.modern:  # как сейчас — классическая полоса со стрелками
            self.rect((x - 14, top, x, bottom), fill=trough)
            for direction in (-1, 1):
                cy = top + 10 if direction < 0 else bottom - 10
                self.d.polygon([(x - 11, cy + 4 * direction), (x - 4, cy - 3 * direction),
                                (x - 3, cy + 4 * direction)], fill=self.p["widget_text"])
            self.rect((x - 12, top + 22, x - 2, top + (bottom - top) * 0.42), fill=thumb)
            self.rect((x - 12, top + 22, x - 2, top + (bottom - top) * 0.42),
                      outline=self.p["widget_text"])
            return
        self.rr((x - 6, top, x - 2, bottom), 2, fill=translucent(self.p["bg"], self.p["muted"], 0.14))
        self.rr((x - 6, top + (bottom - top) * 0.06, x - 2, top + (bottom - top) * 0.44), 2,
                fill=self.p["scroll_thumb"])


# --------------------------------------------------------------------- иконки


def icon_mic(p: Paint, cx, cy, color="#000000"):
    p.rr((cx - 5, cy - 8, cx + 5, cy + 3), 5, fill=color)
    p.d.arc((cx - 9, cy - 2, cx + 9, cy + 12), start=200, end=340, fill=color, width=2)
    p.d.line((cx, cy + 9, cx, cy + 13), fill=color, width=2)


def icon_send(p: Paint, cx, cy, color="#000000"):
    p.d.polygon([(cx - 8, cy - 6), (cx + 8, cy), (cx - 8, cy + 6), (cx - 5, cy)], fill=color)


def icon_speaker(p: Paint, cx, cy, color="#000000"):
    p.d.polygon([(cx - 8, cy - 3), (cx - 4, cy - 3), (cx + 1, cy - 8), (cx + 1, cy + 8),
                 (cx - 4, cy + 3), (cx - 8, cy + 3)], fill=color)
    p.d.arc((cx - 2, cy - 8, cx + 12, cy + 8), start=300, end=60, fill=color, width=2)


def icon_stop(p: Paint, cx, cy, color="#000000"):
    p.rr((cx - 5, cy - 5, cx + 5, cy + 5), 2, fill=color)


def icon_wave(p: Paint, cx, cy, color="#000000"):
    for index, height in enumerate((4, 9, 6, 12, 5)):
        x = cx - 10 + index * 5
        p.d.line((x, cy - height / 2, x, cy + height / 2), fill=color, width=2)


# ------------------------------------------------------- общие части страниц


def draw_modern_chrome(p: Paint, page: str) -> tuple[int, int, int, int]:
    """Шапка, навигация и подвал современного окна. Возвращает область контента."""
    sidebar = p.style["nav"] == "sidebar"
    content_left = (SIDEBAR_W + SPACE["xl"]) if sidebar else MARGIN
    content_right = WIDTH - MARGIN
    top, bottom = HEADER_H + NAV_H + SPACE["m"], HEIGHT - FOOTER_H - INPUT_H - SPACE["l"]

    if sidebar:
        draw_sidebar(p, page)
        state_y = 30
    else:
        p.rect((0, 0, WIDTH, HEADER_H), fill=p.p["surface"])
        p.rr((MARGIN, 12, MARGIN + 32, 44), 9, fill=p.p["accent"])
        icon_wave(p, MARGIN + 16, 28, p.p["on_accent"])
        p.text((MARGIN + 44, 21), "Jarvis", FONTS["heading"], fill=p.p["text"], anchor="lm")
        p.text((MARGIN + 44, 41), "локальный ассистент", FONTS["caption"], fill=p.p["muted"], anchor="lm")
        draw_status_row(p, 300, 30, WIDTH - MARGIN)
        p.d.line((0, HEADER_H, WIDTH, HEADER_H), fill=p.p["border"], width=1)
        draw_nav(p, page, MARGIN, HEADER_H + SPACE["l"], WIDTH - MARGIN * 2)
        state_y = 30

    if sidebar:
        draw_status_row(p, content_left, state_y, content_right)
        p.d.line((content_left, 48, content_right, 48), fill=p.p["border"], width=1)
        top = 66

    p.text((content_left, HEIGHT - 12), "Enter — отправить · Ctrl+L — слушать · Esc — свернуть в трей",
           FONTS["caption"], fill=p.p["muted"], anchor="lm")
    p.text((content_right, HEIGHT - 12), f"макет: {p.style['title'].split('—')[0].strip().lower()} / "
                                         f"{'тёмная' if p.dark else 'светлая'}",
           FONTS["caption"], fill=translucent(p.p["bg"], p.p["muted"], 0.7), anchor="rm")
    return content_left, top, content_right, bottom


def draw_status_row(p: Paint, x: int, y: int, right: int, *, state="Ожидание") -> None:
    dot = p.p["ok"]
    p.glow(x + 6, y - 1, dot)
    p.d.ellipse((x, y - 6, x + 12, y + 6), fill=dot)
    p.text((x + 20, y + 1), state, FONTS["body"], fill=p.p["text"], anchor="lm")
    position = x + 20 + p.width(state, FONTS["body"]) + SPACE["m"]
    position = p.chip(position, y - 12, "Готов слушать", dot=dot) + SPACE["s"]

    chips = [("Модель: нет", None), ("Речь: готова", p.p["ok"]), ("Голос: готов", p.p["ok"])]
    cursor = right
    for label, color in reversed(chips):
        width = p.width(label, FONTS["caption"]) + SPACE["xl"] + (12 if color else 0)
        cursor -= width
        p.chip(cursor, y - 12, label, dot=color)
        cursor -= SPACE["s"]


def draw_nav(p: Paint, page: str, x: int, y: int, width: int) -> None:
    labels = [(key, label) for key, label in PAGES]
    current = dict(PAGES)[page]
    nav = p.style["nav"]
    if nav == "underline":
        cursor = x
        for _, label in labels:
            selected = label == current
            text_w = p.width(label, FONTS["body"])
            p.text((cursor, y + NAV_H / 2), label, FONTS["body"],
                   fill=p.p["accent"] if selected else p.p["muted"], anchor="lm")
            if selected:
                p.rr((cursor, y + NAV_H - 6, cursor + text_w, y + NAV_H - 3), 2, fill=p.p["accent"])
            cursor += text_w + SPACE["xl"]
        return

    total = sum(p.width(label, FONTS["body"]) + 34 for _, label in labels) + SPACE["s"]
    p.rr((x, y + SPACE["s"], x + total, y + NAV_H), (NAV_H - SPACE["s"]) // 2, fill=p.p["surface2"])
    cursor = x + SPACE["xs"]
    for _, label in labels:
        selected = label == current
        box_w = p.width(label, FONTS["body"]) + 30
        if selected:
            p.rr((cursor, y + SPACE["m"], cursor + box_w, y + NAV_H - SPACE["xs"]),
                 (NAV_H - SPACE["l"]) // 2, fill=translucent(p.p["surface2"], p.p["accent"], 0.22))
        p.text((cursor + box_w / 2, y + NAV_H / 2 + 1), label, FONTS["body"],
               fill=p.p["text"] if selected else p.p["muted"], anchor="mm")
        cursor += box_w
    return


def draw_sidebar(p: Paint, page: str) -> None:
    p.rect((0, 0, SIDEBAR_W, HEIGHT), fill=p.p["surface"])
    p.d.line((SIDEBAR_W, 0, SIDEBAR_W, HEIGHT), fill=p.p["border"], width=1)
    p.rr((SPACE["xl"], 20, SPACE["xl"] + 30, 50), 10, fill=p.p["accent"])
    icon_wave(p, SPACE["xl"] + 15, 35, p.p["on_accent"])
    p.text((SPACE["xl"] + 40, 28), "Jarvis", FONTS["heading"], fill=p.p["text"], anchor="lm")
    p.text((SPACE["xl"] + 40, 47), "локальный ассистент", FONTS["caption"], fill=p.p["muted"], anchor="lm")

    current = dict(PAGES)[page]
    y = 84
    for _, label in PAGES:
        selected = label == current
        if selected:
            p.rr((SPACE["m"], y, SIDEBAR_W - SPACE["m"], y + 38), 6, fill=p.p["surface2"])
        p.text((SPACE["xl"] + 10, y + 19), label, FONTS["body"],
               fill=p.p["accent"] if selected else p.p["muted"], anchor="lm")
        y += 44

    p.d.line((SPACE["m"], HEIGHT - 92, SIDEBAR_W - SPACE["m"], HEIGHT - 92), fill=p.p["border"], width=1)
    p.text((SPACE["xl"] + 10, HEIGHT - 70), "Тема: как в системе", FONTS["caption"],
           fill=p.p["muted"], anchor="lm")
    p.toggle(SPACE["xl"] + 10, HEIGHT - 52, on_=p.dark)
    p.text((SPACE["xl"] + 60, HEIGHT - 41), "тёмная", FONTS["caption"], fill=p.p["muted"], anchor="lm")


# ------------------------------------------------------------------ страницы


def draw_chat(p: Paint, area) -> None:
    left, top, right, bottom = area
    bubbles = p.style["bubbles"]
    scroll_x = right - 6
    p.scrollbar(scroll_x, top, bottom)
    right = scroll_x - SPACE["m"]
    y = top + SPACE["s"]

    for role, text in MESSAGES:
        mine = role == "user"
        pad_x, pad_y = SPACE["l"], SPACE["m"]
        body_h = FONTS["body"] + SPACE["s"]
        if bubbles["user"] == "plain":
            width = p.width(text, FONTS["body"])
            if mine:
                p.text((right - 6, y + 18), text, FONTS["body"], fill=p.p["text"], anchor="ra")
            else:
                p.text((left + 6, y), "Jarvis", FONTS["caption"], fill=p.p["accent"], anchor="la")
                p.text((left + 6, y + 18), text, FONTS["body"], fill=p.p["text"], anchor="la")
            y += 18 + body_h + SPACE["l"]
            continue

        box_w = min(p.width(text, FONTS["body"]) + pad_x * 2, right - left - 60)
        box_h = body_h + pad_y * 2 - SPACE["s"]
        if mine:
            box = (right - box_w, y, right, y + box_h)
            if bubbles["user"] == "outline":
                p.rr(box, p.r["bubble"], fill=p.p["user_bg"], outline=p.p["user_border"], width=1)
            else:
                p.shadow(box, p.r["bubble"])
                p.rr(box, p.r["bubble"], fill=p.p["user_bg"])
            p.text((box[0] + pad_x, box[1] + pad_y), text, FONTS["body"], fill=p.p["user_text"], anchor="la")
        else:
            box = (left, y, left + box_w, y + box_h)
            p.shadow(box, p.r["bubble"])
            p.rr(box, p.r["bubble"], fill=p.p["assistant_bg"], outline=p.p["assistant_border"], width=1)
            if bubbles["assistant"] == "bar":
                p.rr((box[0] + SPACE["s"], box[1] + SPACE["s"], box[0] + SPACE["s"] + 3,
                      box[3] - SPACE["s"]), 2, fill=p.p["accent"])
                p.text((box[0] + pad_x + SPACE["xs"], box[1] + pad_y), text, FONTS["body"],
                       fill=p.p["assistant_text"], anchor="la")
            else:
                p.text((box[0] + pad_x, box[1] + pad_y), text, FONTS["body"],
                       fill=p.p["assistant_text"], anchor="la")
        y += box_h + SPACE["m"]


def draw_input_row(p: Paint, area) -> None:
    left, top, right, bottom = area
    y = top + (bottom - top - INPUT_H) // 2

    mic = (left, y, left + INPUT_H, y + INPUT_H)
    p.rr(mic, INPUT_H // 2, fill=p.p["surface2"])
    if p.style.get("glow"):
        p.rr(mic, INPUT_H // 2, fill=None, outline=p.p["accent"], width=1)
    icon_mic(p, mic[0] + INPUT_H / 2, mic[1] + INPUT_H / 2, p.p["text"])

    send_label = "Отправить"
    send_w = SPACE["l"] * 2 + 20 + p.width(send_label, FONTS["body"])
    send = (right - send_w, y, right, y + INPUT_H)
    p.rr(send, p.r["button"], fill=p.p["accent"])
    icon_send(p, send[0] + SPACE["l"] + 8, send[1] + INPUT_H / 2, p.p["on_accent"])
    p.text((send[0] + SPACE["l"] + 24, send[1] + INPUT_H / 2 + 1), send_label, FONTS["body"],
           fill=p.p["on_accent"], anchor="lm")

    speak_label = "Озвучивать"
    speak_w = SPACE["l"] * 2 + 20 + p.width(speak_label, FONTS["body"])
    speak = (send[0] - speak_w - SPACE["s"], y, send[0] - SPACE["s"], y + INPUT_H)
    p.rr(speak, p.r["field"], fill=p.p["input_bg"], outline=p.p["border"], width=1)
    icon_speaker(p, speak[0] + SPACE["l"] + 8, speak[1] + INPUT_H / 2, p.p["accent"])
    p.text((speak[0] + SPACE["l"] + 24, speak[1] + INPUT_H / 2 + 1), speak_label, FONTS["body"],
           fill=p.p["text"], anchor="lm")

    field = (mic[2] + SPACE["s"], y, speak[0] - SPACE["s"], y + INPUT_H)

    def trailing(paint: Paint, box):
        stop = (box[2] - 34, box[1] + (INPUT_H - 28) / 2, box[2] - 6, box[1] + (INPUT_H + 28) / 2)
        paint.rr(stop, 6, fill=translucent(paint.p["input_bg"], paint.p["err"], 0.22))
        icon_stop(paint, stop[0] + 14, stop[1] + 14, paint.p["err"])

    p.field(field, "Спросите что-нибудь или нажмите микрофон", placeholder=True,
            focus=True, trailing=trailing)


def draw_skill_cards(p: Paint, area) -> None:
    left, top, right, bottom = area
    skills = [
        ("Время и дата", "Текущее время, дата, день недели", ["Без доступа к системе"], "5 действий", True),
        ("Состояние системы", "Память, диск, процессор, батарея", ["Только чтение"], "5 действий", True),
        ("Громкость и медиа", "Тише, громче, пауза, следующий трек",
         ["Управление звуком", "Подтверждение"], "7 действий", True),
        ("Питание", "Блокировка, перезагрузка, выключение",
         ["Системные команды", "Подтверждение"], "6 действий", True),
        ("Снимок экрана", "Сохраняет картинку экрана в папку снимков", ["Доступ к экрану"], "2 действия", False),
        ("Поиск в интернете", "Ответ на вопрос по свежим данным", ["Сеть"], "1 действие", True),
    ]
    scroll_x = right - 6
    p.scrollbar(scroll_x, top, bottom)
    right = scroll_x - SPACE["s"]
    y = top
    for title, description, chips, actions, enabled in skills:
        card_h = 78
        box = (left, y, right, y + card_h)
        p.shadow(box)
        p.rr(box, p.r["card"], fill=p.p["surface"], outline=p.p["border"], width=1)
        p.text((left + SPACE["xl"], y + 26), title, FONTS["body"],
               fill=p.p["text"] if enabled else p.p["muted"], anchor="lm")
        cursor = left + SPACE["xl"] + p.width(title, FONTS["body"]) + SPACE["m"]
        for chip in chips:
            cursor = p.chip(cursor, y + 15, chip, pad=SPACE["s"], height=22) + SPACE["xs"]
        p.text((left + SPACE["xl"], y + 52), description, FONTS["caption"], fill=p.p["muted"], anchor="lm")
        p.text((left + SPACE["xl"] + p.width(description, FONTS["caption"]) + SPACE["m"], y + 52),
               actions, FONTS["caption"], fill=p.p["muted"], anchor="lm")
        p.toggle(right - 76, y + 28, on_=enabled)
        p.text((right - 86, y + 39), "вкл" if enabled else "выкл", FONTS["caption"],
               fill=p.p["accent"] if enabled else p.p["muted"], anchor="rm")
        y += card_h + SPACE["m"]
    p.text((left, y + 4), "Навыков: 32 (включено 31)", FONTS["caption"], fill=p.p["muted"], anchor="lm")


def draw_settings(p: Paint, area) -> None:
    left, top, right, bottom = area
    y = top
    sections = [
        ("Модель и поиск", [
            ("Модель", "gpt-4o-mini", "field"),
            ("Адрес сервиса", "https://api.omniroute.io/v1", "field"),
            ("Ключ модели", "••••••••••••  ключ уже сохранён в .env", "mask"),
        ]),
        ("Голос", [
            ("Отвечать вслух", "включено", "toggle"),
            ("Голос синтеза", "ru_RU-irina-medium", "combo"),
        ]),
        ("Интерфейс", [
            ("Тема", "как в системе", "combo"),
            ("Значок в трее", "включено", "toggle"),
            ("Горячая клавиша", "Super+J", "field"),
        ]),
    ]
    for title, rows in sections:
        card_h = 52 + len(rows) * 42
        box = (left, y, right, y + card_h)
        p.shadow(box)
        p.rr(box, p.r["card"], fill=p.p["surface"], outline=p.p["border"], width=1)
        p.text((left + SPACE["xl"], y + 28), title, FONTS["body"], fill=p.p["text"], anchor="lm")
        row_y = y + 52
        for index, (label, value, kind) in enumerate(rows):
            p.text((left + SPACE["xl"], row_y + 21), label, FONTS["caption"],
                   fill=p.p["muted"], anchor="lm")
            control_x, control_w = left + 300, right - (left + 300) - SPACE["xl"]
            if kind == "field":
                p.field((control_x, row_y + 6, control_x + control_w, row_y + 40), value)
            elif kind == "mask":
                field_w = control_w - 170
                p.field((control_x, row_y + 6, control_x + field_w, row_y + 40), value)
                p.button(control_x + field_w + SPACE["s"], row_y + 6, "Сохранить ключ", height=34)
            elif kind == "combo":
                p.field((control_x, row_y + 6, control_x + 220, row_y + 40), value)
                p.d.polygon([(control_x + 200, row_y + 20), (control_x + 210, row_y + 20),
                             (control_x + 205, row_y + 26)], fill=p.p["muted"])
            elif kind == "toggle":
                p.toggle(control_x, row_y + 12, on_=value == "включено")
            if index < len(rows) - 1:
                p.d.line((left + SPACE["xl"], row_y + 46, right - SPACE["xl"], row_y + 46),
                         fill=p.p["border"], width=1)
            row_y += 42
        y += card_h + SPACE["m"]

    # кнопка сохранения не должна налезать на подсказки в подвале
    button_y = min(y + SPACE["s"], HEIGHT - FOOTER_H - 46)
    p.button(left, button_y, "Сохранить настройки", primary=True, height=38)
    p.text((left + 220, button_y + 20), "Изменений нет", FONTS["caption"],
           fill=p.p["muted"], anchor="lm")


def draw_journal(p: Paint, area) -> None:
    left, top, right, bottom = area
    y = top
    cursor = left
    for label in ("Все", "Информация", "Предупреждения", "Ошибки"):
        selected = label == "Все"
        cursor = p.chip(cursor, y, label, pad=SPACE["m"], height=28,
                        fg=p.p["on_accent"] if selected else p.p["muted"],
                        bg=p.p["accent"] if selected else p.p["chip_bg"]) + SPACE["s"]
    y += 36
    # поиск и кнопки — отдельной строкой: фильтры длинные и не должны на них налезать
    p.field((left, y, left + 260, y + 28), "Поиск по журналу", placeholder=True)
    cursor = left + 272
    cursor = p.button(cursor, y, "Обновить", height=28) + SPACE["s"]
    p.button(cursor, y, "Скопировать для отчёта", height=28)
    y += 40

    lines = [
        ("INFO", "19:41:02", "gui.start", "окно открыто, ядро на 127.0.0.1:51834"),
        ("INFO", "19:41:05", "skill.time", "«сколько времени» → 19:41"),
        ("INFO", "19:41:22", "llm.openai", "ответ получен за 0.8 с, модель gpt-4o-mini"),
        ("WARN", "19:41:40", "provider.tts", "голос не найден, ответ без озвучки"),
        ("INFO", "19:42:10", "skill.power", "«выключи компьютер» → нужно подтверждение"),
        ("INFO", "19:42:14", "skill.power", "подтверждение отклонено пользователем"),
        ("ERROR", "19:43:01", "provider.stt", "микрофон занят другим приложением"),
        ("INFO", "19:43:30", "gui.copy", "сведения для отчёта скопированы, скрыто 3 секрета"),
    ]
    level_color = {"INFO": p.p["ok"], "WARN": p.p["warn"], "ERROR": p.p["err"]}
    box = (left, y, right, bottom - 26)
    p.rr(box, p.r["card"], fill=p.p["surface"], outline=p.p["border"], width=1)
    line_y = y + 20
    for level, time, source, message in lines:
        p.rr((left + SPACE["l"], line_y - 11, left + 78, line_y + 11), 5,
             fill=translucent(p.p["surface"], level_color[level], 0.18))
        p.text((left + SPACE["l"] + 31, line_y + 1), level, FONTS["mono"], kind="mono",
               fill=level_color[level], anchor="mm")
        p.text((left + 92, line_y + 1), time, FONTS["mono"], kind="mono", fill=p.p["muted"], anchor="lm")
        p.text((left + 168, line_y + 1), source, FONTS["mono"], kind="mono", fill=p.p["text"], anchor="lm")
        p.text((left + 300, line_y + 1), message, FONTS["mono"], kind="mono", fill=p.p["muted"], anchor="lm")
        line_y += 32
    p.text((left, bottom - 10), "Показаны последние 200 событий из journal.jsonl",
           FONTS["caption"], fill=p.p["muted"], anchor="lm")


def draw_about(p: Paint, area) -> None:
    left, top, right, bottom = area
    box = (left, top, right, bottom)
    p.rr(box, p.r["card"], fill=p.p["surface"], outline=p.p["border"], width=1)
    p.rr((left + SPACE["xl"], top + SPACE["xl"], left + SPACE["xl"] + 34, top + SPACE["xl"] + 34),
         10, fill=p.p["accent"])
    icon_wave(p, left + SPACE["xl"] + 17, top + SPACE["xl"] + 17, p.p["on_accent"])
    lines = [
        ("Jarvis 1.0.0", p.p["text"], FONTS["heading"]),
        ("Ядро: http://127.0.0.1:51834", p.p["muted"], FONTS["body"]),
        ("Настройки: C:\\Users\\user\\AppData\\Local\\Jarvis\\config", p.p["muted"], FONTS["body"]),
        ("Данные: C:\\Users\\user\\AppData\\Local\\Jarvis\\state", p.p["muted"], FONTS["body"]),
        ("Использованные проекты и лицензии:", p.p["text"], FONTS["body"]),
        ("• Piper (MIT) — синтез речи", p.p["muted"], FONTS["body"]),
        ("• whisper.cpp (MIT) — распознавание речи", p.p["muted"], FONTS["body"]),
        ("• openWakeWord (Apache-2.0) — слово-активатор", p.p["muted"], FONTS["body"]),
        ("• pystray (LGPL-3.0), Pillow (HPND) — значок в трее", p.p["muted"], FONTS["body"]),
        ("Ключи хранятся только в .env с правами 600.", p.p["muted"], FONTS["body"]),
    ]
    y = top + SPACE["xl"] + 56
    for text, color, size in lines:
        p.text((left + SPACE["xl"], y), text, size, fill=color, anchor="lm")
        y += 26 if size == FONTS["body"] else 30
    p.button(left + SPACE["xl"], bottom - 56, "Открыть папку настроек", height=34)
    p.button(left + SPACE["xl"] + 210, bottom - 56, "Скопировать сведения", height=34)


# -------------------------------------------------- воссоздание текущего окна


def draw_before(p: Paint, page: str) -> None:
    """Текущее окно: те же элементы, что в Этапе 2, с его огрехами."""
    palette = p.p
    p.rect((0, 0, WIDTH, HEIGHT), fill=palette["bg"])
    # шапка: тёмная панель, серый кружок, провайдеры справа
    p.rect((0, 0, WIDTH, 52), fill=palette["surface"])
    p.d.ellipse((14, 15, 36, 37), fill="#7d8590")
    p.text((46, 26), "Ожидание", FONTS["body"], kind="sans_bold", fill=palette["text"], anchor="lm")
    providers = "Модель: нет · Распознавание: готов · Голос: готов"
    p.text((WIDTH - 14 - p.width(providers, FONTS["caption"]), 26), providers, FONTS["caption"],
           fill=palette["muted"], anchor="lm")

    # вкладки ttk.Notebook в теме clam: светлые «объёмные» кнопки в тёмном окне
    tab_y = 52 + 8
    cursor = 10
    for key, label in PAGES:
        selected = key == page
        width = p.width(label, FONTS["body"]) + 28
        box = (cursor, tab_y, cursor + width, tab_y + 26)
        if selected:
            p.rect(box, fill="#2f81f7")
            p.text((box[0] + 14, tab_y + 14), label, FONTS["body"], fill="#ffffff", anchor="lm")
        else:
            p.rect(box, fill="#f0f0f0", outline="#a9a9a9")
            p.text((box[0] + 14, tab_y + 14), label, FONTS["body"], fill="#1f2328", anchor="lm")
        cursor = box[2] + 2
    frame_top = tab_y + 26

    content = (10, frame_top + 10, WIDTH - 10, HEIGHT - 68)
    if page == "chat":
        # большое чёрное поле текста и классическая полоса прокрутки
        p.rect((content[0], content[1], content[2], content[3]), fill=palette["input_bg"])
        p.d.rectangle((content[0], content[1], content[2], content[3]), outline=palette["border"])
        y = content[1] + 12
        for role, text in MESSAGES:
            who = "Вы" if role == "user" else "Джарвис"
            color = palette["user_text"] if role == "user" else palette["assistant_text"]
            p.text((content[0] + 10, y), f"{who}: {text}", FONTS["body"], fill=color, anchor="lm")
            y += 30
        p.scrollbar(content[2] - 2, content[1], content[3])
    elif page == "skills":
        draw_before_skills(p, content)
    elif page == "settings":
        draw_before_settings(p, content)
    elif page == "journal":
        draw_before_journal(p, content)
    else:
        draw_before_about(p, content)

    # строка ввода: светлые clam-кнопки и чекбокс в тёмном окне
    row_y = HEIGHT - 62
    p.rect((22, row_y, 232, row_y + 30), fill="#f0f0f0", outline="#a9a9a9")
    p.text((127, row_y + 15), "Слушать", FONTS["body"], fill="#1f2328", anchor="mm")
    p.rect((238, row_y - 2, WIDTH - 330, row_y + 32), fill="#ffffff", outline="#a9a9a9")
    p.rect((242, row_y + 2, 245, row_y + 28), fill="#2f81f7")
    p.rect((WIDTH - 320, row_y + 2, WIDTH - 306, row_y + 16), fill="#ffffff", outline="#a9a9a9")
    p.d.line((WIDTH - 317, row_y + 9, WIDTH - 314, row_y + 13), fill="#2f81f7", width=2)
    p.d.line((WIDTH - 314, row_y + 13, WIDTH - 309, row_y + 5), fill="#2f81f7", width=2)
    p.text((WIDTH - 300, row_y + 15), "Озвучивать", FONTS["body"], fill=palette["text"], anchor="lm")
    p.rect((WIDTH - 216, row_y, WIDTH - 112, row_y + 30), fill="#f0f0f0", outline="#a9a9a9")
    p.text((WIDTH - 164, row_y + 15), "Отправить", FONTS["body"], fill="#1f2328", anchor="mm")

    # подвал: подпись про трей длинная и залезает на подсказку справа,
    # а часть подсказки вообще уходит за край окна (так выглядит сейчас)
    p.rect((0, HEIGHT - 30, WIDTH, HEIGHT), fill=palette["surface"])
    p.text((12, HEIGHT - 15), "Трей недоступен: нет библиотеки pystray (No module named 'pystray')",
           FONTS["caption"], fill=palette["muted"], anchor="lm")
    p.text((WIDTH - 316, HEIGHT - 15), "Чтобы вызвать ассистента, нажмите Super+J",
           FONTS["caption"], fill=palette["muted"], anchor="lm")
    p.text((content[0] + 6, content[3] - 12), f"макет: как сейчас / {'тёмная' if p.dark else 'светлая'}",
           FONTS["caption"], fill=translucent(palette["bg"], palette["muted"], 0.55), anchor="lm")


def draw_before_skills(p: Paint, content) -> None:
    left, top, right, bottom = content
    columns = [("Навык", 250), ("Состояние", 120), ("Права", 300)]
    p.rect((left, top, right, top + 26), fill="#f0f0f0", outline="#a9a9a9")
    cursor = left + 8
    for title, width in columns:
        p.text((cursor, top + 13), title, FONTS["body"], fill="#1f2328", anchor="lm")
        cursor += width
    rows = [("Время и дата", "включён", "—"), ("Состояние системы", "включён", "Только чтение"),
            ("Громкость и медиа", "включён", "Управление звуком"), ("Питание", "включён", "Системные команды"),
            ("Снимок экрана", "выключен", "Доступ к экрану")]
    y = top + 26
    for index, row in enumerate(rows):
        selected = index == 2
        p.rect((left, y, right, y + 26), fill="#2f81f7" if selected else p.p["surface"])
        cursor = left + 8
        for value, (_, width) in zip(row, columns):
            p.text((cursor, y + 13), value, FONTS["body"],
                   fill="#ffffff" if selected else p.p["text"], anchor="lm")
            cursor += width
        y += 26
    field = (left, y, right, y + 24)
    p.rect(field, fill="#ffffff", outline="#a9a9a9")
    details = (left, bottom - 150, right, bottom - 46)
    p.rect(details, fill=p.p["input_bg"], outline=p.p["border"])
    p.text((details[0] + 10, details[1] + 16), "Громкость и медиа — управление звуком",
           FONTS["body"], fill=p.p["text"], anchor="lm")
    for index, line in enumerate(["• Тише — «тише» (спросит подтверждение)", "• Громче — «громче»",
                                  "• Пауза — «пауза»", "• Следующий трек — «следующий трек»"]):
        p.text((details[0] + 10, details[1] + 44 + index * 20), line, FONTS["caption"],
               fill=p.p["muted"], anchor="lm")
    y = bottom - 36
    cursor = left
    for label in ("Включить", "Выключить", "Обновить"):
        width = p.width(label, FONTS["body"]) + 24
        p.rect((cursor, y, cursor + width, y + 28), fill="#f0f0f0", outline="#a9a9a9")
        p.text((cursor + width / 2, y + 14), label, FONTS["body"], fill="#1f2328", anchor="mm")
        cursor += width + 8
    p.text((cursor + 4, y + 14), "Навыков: 32 (включено 31)", FONTS["caption"],
           fill=p.p["muted"], anchor="lm")


def draw_before_settings(p: Paint, content) -> None:
    left, top, right, bottom = content
    sections = [
        ("Модель и поиск", [("Модель", "gpt-4o-mini"), ("Адрес сервиса", "https://api.omniroute.io/v1")]),
        ("Голос", [("Отвечать вслух", "включено"), ("Голос синтеза", "ru_RU-irina-medium")]),
        ("Интерфейс", [("Тема", "dark"), ("Горячая клавиша", "Super+J")]),
    ]
    y = top + 10
    for title, rows in sections:
        p.text((left + 10, y + 12), title, FONTS["body"], kind="sans_bold", fill=p.p["text"], anchor="lm")
        y += 28
        for label, value in rows:
            p.text((left + 10, y + 15), label, FONTS["body"], fill=p.p["text"], anchor="lm")
            p.rect((left + 230, y, left + 560, y + 26), fill="#ffffff", outline="#a9a9a9")
            p.text((left + 238, y + 13), value, FONTS["body"], fill="#1f2328", anchor="lm")
            y += 32
        y += 6
    p.rect((left + 10, y + 6, left + 130, y + 38), fill="#f0f0f0", outline="#a9a9a9")
    p.text((left + 70, y + 22), "Сохранить", FONTS["body"], fill="#1f2328", anchor="mm")
    p.text((left + 150, y + 22), "Изменений нет", FONTS["caption"], fill=p.p["muted"], anchor="lm")


def draw_before_journal(p: Paint, content) -> None:
    left, top, right, bottom = content
    p.text((left + 10, top + 12), "Уровень", FONTS["body"], fill=p.p["text"], anchor="lm")
    p.rect((left + 80, top, left + 170, top + 24), fill="#ffffff", outline="#a9a9a9")
    p.text((left + 88, top + 12), "все", FONTS["body"], fill="#1f2328", anchor="lm")
    p.text((left + 186, top + 12), "Поиск", FONTS["body"], fill=p.p["text"], anchor="lm")
    p.rect((left + 240, top, left + 420, top + 24), fill="#ffffff", outline="#a9a9a9")
    for index, label in enumerate(("Обновить", "Скопировать")):
        x = left + 432 + index * 110
        p.rect((x, top - 2, x + 100, top + 26), fill="#f0f0f0", outline="#a9a9a9")
        p.text((x + 50, top + 12), label, FONTS["body"], fill="#1f2328", anchor="mm")
    box = (left, top + 34, right, bottom - 26)
    p.rect(box, fill=p.p["input_bg"], outline=p.p["border"])
    y = top + 48
    for level, time, source, message in [
        ("INFO", "19:41:02", "gui.start", "окно открыто"),
        ("WARN", "19:41:40", "provider.tts", "голос не найден"),
        ("ERROR", "19:43:01", "provider.stt", "микрофон занят"),
    ]:
        p.text((left + 10, y), f"{time}  {level:<5}  {source}: {message}", FONTS["body"],
               fill=p.p["text"], anchor="lm")
        y += 24
    p.text((left + 10, bottom - 12), "Последние события. Секреты при копировании вырезаются.",
           FONTS["caption"], fill=p.p["muted"], anchor="lm")


def draw_before_about(p: Paint, content) -> None:
    left, top, right, bottom = content
    p.rect((left, top, right, bottom - 46), fill=p.p["input_bg"], outline=p.p["border"])
    for index, line in enumerate([
        "Программа: Jarvis 1.0.0", "Ядро: http://127.0.0.1:51834",
        "Настройки: C:\\Users\\user\\AppData\\Local\\Jarvis\\config",
        "Данные: C:\\Users\\user\\AppData\\Local\\Jarvis\\state", "",
        "Использованные проекты и лицензии:",
        "• Piper (rhasspy/piper) — MIT: синтез речи",
        "• whisper.cpp (ggerganov) — MIT: распознавание речи",
        "• openWakeWord (dscripay) — Apache-2.0: слово-активатор",
        "• pystray, Pillow — LGPL/HPND: значок в трее"]):
        p.text((left + 12, top + 18 + index * 22), line, FONTS["body"], fill=p.p["text"], anchor="lm")
    p.rect((left, bottom - 38, left + 190, bottom - 8), fill="#f0f0f0", outline="#a9a9a9")
    p.text((left + 95, bottom - 23), "Открыть папку настроек", FONTS["caption"], fill="#1f2328", anchor="mm")
    p.rect((left + 200, bottom - 38, left + 370, bottom - 8), fill="#f0f0f0", outline="#a9a9a9")
    p.text((left + 285, bottom - 23), "Скопировать сведения", FONTS["caption"], fill="#1f2328", anchor="mm")


# -------------------------------------------------------------------- рендер


def render(style_name: str, page: str, theme: str, out_path: Path) -> Path:
    from PIL import Image, ImageDraw

    style = STYLES[style_name]
    palette = style["palette"][theme]
    image = Image.new("RGBA", (WIDTH, HEIGHT), palette["bg"])
    draw = ImageDraw.Draw(image)
    p = Paint(draw, style, palette)

    if style["kind"] == "classic":
        draw_before(p, page)
    else:
        area = draw_modern_chrome(p, page)
        if page == "chat":
            draw_chat(p, area)
            draw_input_row(p, (area[0], area[3] + SPACE["xs"], area[2], area[3] + INPUT_H + SPACE["xs"]))
        else:
            # на страницах без строки ввода низ можно занять целиком
            full = (area[0], area[1], area[2], HEIGHT - FOOTER_H - SPACE["m"])
            if page == "skills":
                draw_skill_cards(p, full)
            elif page == "settings":
                draw_settings(p, full)
            elif page == "journal":
                draw_journal(p, full)
            else:
                draw_about(p, full)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    image.convert("RGB").save(out_path)
    return out_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Нарисовать макет интерфейса Jarvis")
    parser.add_argument("--style", default="fluent", choices=[*STYLES, "all", "tokens"])
    parser.add_argument("--page", default="chat", choices=[item[0] for item in PAGES])
    parser.add_argument("--theme", default="dark", choices=["dark", "light", "both"])
    parser.add_argument("--out-dir", default=str(PROJECT_ROOT / "docs" / "style"))
    args = parser.parse_args(argv)

    try:
        import PIL  # noqa: F401
    except ImportError:
        print("Нужен Pillow (только для этого инструмента): pip install Pillow", file=sys.stderr)
        return 1

    if args.style == "tokens":  # лист токенов темы — по одному на тему
        themes = ["dark", "light"] if args.theme == "both" else [args.theme]
        for theme_name in themes:
            print("Готово:", render_tokens(theme_name, Path(args.out_dir) / f"tokens-{theme_name}.png"))
        return 0

    styles = list(STYLES) if args.style == "all" else [args.style]
    themes = ["dark", "light"] if args.theme == "both" else [args.theme]
    pages = [item[0] for item in PAGES] if (args.style == "all" or args.page == "all") else [args.page]
    for style_name in styles:
        for page in pages:
            for theme in themes:
                name = f"{style_name}-{page}-{theme}.png"
                print("Готово:", render(style_name, page, theme, Path(args.out_dir) / name))
    return 0




# ------------------------------------------------- лист токенов (шпаргалка темы)


def render_tokens(theme_name: str, out_path: Path) -> Path:
    """Лист токенов темы: палитра, шкалы, шрифты, состояния, контраст.

    Рисуется прямо из ``jarvis.interfaces.theme`` — это и документация, и
    проверка глазами: поменяли цвет или размер в теме, картинка обновилась.
    """
    from PIL import Image, ImageDraw

    from jarvis.interfaces import theme as tokens

    palette = tokens.PALETTES[theme_name]
    width, height = 1180, 860
    image = Image.new("RGBA", (width, height), palette["bg"])
    draw = ImageDraw.Draw(image)
    ink, soft, line = palette["text"], palette["muted"], palette["border"]
    radius, space = tokens.RADIUS, tokens.SPACE

    def text(x, y, string, size=15, color=None, bold=False, mono=False, anchor="la"):
        kind = "mono" if mono else ("sans_bold" if bold else "sans")
        draw.text((x, y), string, font=font(kind, size), fill=color or ink, anchor=anchor)

    def card(box):
        draw.rounded_rectangle(box, radius=radius["card"], fill=palette["surface"], outline=line)

    text(28, 24, f"Jarvis — тема «{theme_name}»", 24, ink, bold=True)
    text(28, 58, "цвета, отступы, шрифты и скругления окна живут в "
                 "jarvis/interfaces/theme.py", 15, soft)

    # ---------------------------------------------------------------- палитра
    px = 28
    card((px - 16, 88, px + 452, 640))
    text(px, 102, "Палитра", 17, ink, bold=True)
    roles = [
        ("bg", "фон окна"), ("surface", "карточка, панель"), ("surface2", "поле, вторичная кнопка"),
        ("input_bg", "поле ввода"), ("border", "граница"), ("field_border", "граница поля"),
        ("text", "основной текст"), ("muted", "подпись"), ("accent", "акцент"),
        ("on_accent", "текст на акценте"), ("user_bg", "плашка пользователя"),
        ("assistant_bg", "плашка ассистента"), ("ok", "хорошо"), ("warn", "внимание"), ("err", "ошибка"),
    ]
    row = 134
    for key, title in roles:
        draw.rounded_rectangle((px, row, px + 34, row + 22), radius=4, fill=palette[key], outline=line)
        text(px + 46, row + 11, f"{key:<14} {palette[key]}", 14, ink, mono=True, anchor="lm")
        text(px + 268, row + 11, title, 14, soft, anchor="lm")
        row += 33

    # -------------------------------------------------------- шкалы (колонка 2)
    sx = 500
    card((sx - 16, 88, sx + 320, 318))
    text(sx, 102, "Шкала отступов (px)", 17, ink, bold=True)
    bar = 138
    for name, value in space.items():
        draw.rounded_rectangle((sx, bar, sx + value * 3, bar + 18), radius=3, fill=palette["accent"])
        text(sx + 200, bar + 9, f"{value} · {name}", 14, ink, mono=True, anchor="lm")
        bar += 36

    card((sx - 16, 318, sx + 320, 520))
    text(sx, 332, "Скругления (px)", 17, ink, bold=True)
    box_x = sx
    for name, value in (("button", radius["button"]), ("card", radius["card"]),
                        ("bubble", radius["bubble"]), ("pill", 14)):
        draw.rounded_rectangle((box_x, 368, box_x + 60, 418), radius=min(value, 26),
                               fill=palette["surface2"], outline=line)
        text(box_x + 30, 432, name, 13, soft, anchor="mm")
        text(box_x + 30, 450, str(value), 13, ink, mono=True, anchor="mm")
        box_x += 76

    card((sx - 16, 520, sx + 320, 700))
    text(sx, 534, "Состояния индикатора", 17, ink, bold=True)
    state_x, state_y, index = sx, 572, 0
    for name, color in tokens.STATE_COLORS[theme_name].items():
        draw.ellipse((state_x, state_y - 8, state_x + 16, state_y + 8), fill=color)
        text(state_x + 24, state_y, name, 13, soft, anchor="lm")
        index += 1
        if index == 3:                      # две колонки по три состояния
            state_x, state_y = sx + 150, 572
        else:
            state_y += 32

    # ------------------------------------------------------ шрифты (колонка 3)
    fx = 856
    card((fx - 16, 88, fx + 308, 400))
    text(fx, 102, "Шкала шрифтов", 17, ink, bold=True)
    samples = {"title": "Jarvis", "heading": "Настройки", "body": "Спросите что-нибудь",
               "caption": "подпись", "mono": "journal.jsonl"}
    font_y = 142
    for name, spec in tokens.FONTS.items():
        text(fx, font_y, samples[name], spec["size"], ink,
             bold=spec.get("weight") == "bold", mono=name == "mono")
        text(fx + 280, font_y + 6, f"{name} {spec['size']}", 13, soft, anchor="ra")
        font_y += spec["size"] + 20
    text(fx, 330, "Segoe UI Variable / Segoe UI — Windows", 13, soft)
    text(fx, 352, "Noto Sans / DejaVu Sans — Linux", 13, soft)
    text(fx, 374, "моно: Cascadia Mono · Consolas · DejaVu Sans Mono", 13, soft)

    card((fx - 16, 400, fx + 308, 700))
    text(fx, 414, "Состояния кнопки", 17, ink, bold=True)
    states = [
        ("обычная", palette["surface2"], ink, False),
        ("наведение", palette["chip_bg"], ink, False),
        ("нажатие", palette["surface"], ink, False),
        ("недоступна", translucent(palette["bg"], palette["muted"], 0.14), palette["muted"], False),
        ("фокус", palette["accent"], palette["on_accent"], True),
    ]
    cursor_y = 452
    for name, fill, foreground, focused in states:
        draw.rounded_rectangle((fx, cursor_y, fx + 118, cursor_y + 34), radius=radius["button"],
                               fill=fill, outline=palette["accent"] if focused else line,
                               width=2 if focused else 1)
        text(fx + 59, cursor_y + 18, "Отправить", 14, foreground, anchor="mm")
        text(fx + 132, cursor_y + 18, name, 13, soft, anchor="lm")
        cursor_y += 44

    # ------------------------------------------------------------ контраст
    rows = tokens.audit(theme_name)
    worst = min(rows, key=lambda item: item[1] / item[2])
    text(28, 736, "Контраст по WCAG:", 15, ink, bold=True)
    text(28, 762, f"пар проверено {len(rows)} · ниже порога {sum(1 for row in rows if not row[3])}"
                  f" · ближайшая к порогу — {worst[1]}:1 из {worst[2]}:1 ({worst[0]})", 13, soft)
    text(28, 786, "правишь цвет в теме — эта картинка и tools/contrast_check.py меняются вместе с ним",
         13, soft)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    image.convert("RGB").save(out_path)
    return out_path


if __name__ == "__main__":
    raise SystemExit(main())
