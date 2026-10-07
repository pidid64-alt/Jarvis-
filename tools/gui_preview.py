#!/usr/bin/env python3
"""Предпросмотр окна Jarvis в виде картинки — для документации и проверки вида.

Зачем это нужно: настоящее окно требует Tkinter и графической среды, а картинку
для README и отчёта нужно собрать и на сервере без экрана. Скрипт берёт цвета и
подписи **из самого кода окна** (``jarvis.interfaces.gui``) и рисует макет
главной страницы: шапку с индикатором состояния, вкладки, ленту диалога, строку
ввода и подсказку внизу.

Это не снимок работающего окна, а предпросмотр: он показывает цвета, шрифты и
расположение элементов, но не заменяет проверку на живой системе.

Запуск (нужен Pillow — только для этого инструмента, приложению он не нужен)::

    python tools/gui_preview.py --theme dark --out docs/gui-preview-dark.png
    python tools/gui_preview.py --theme light --out docs/gui-preview-light.png
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

WIDTH, HEIGHT = 880, 640
HEADER_H = 52
TABS_H = 34
FOOTER_H = 30
FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
    "C:/Windows/Fonts/segoeui.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
)


def load_font(size: int, bold: bool = False):
    from PIL import ImageFont

    for path in FONT_CANDIDATES:
        if Path(path).exists():
            if bold and "DejaVuSans.ttf" in path:
                path = path.replace("DejaVuSans.ttf", "DejaVuSans-Bold.ttf")
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def draw_tabs(draw, palette: dict[str, str], tab: str, labels: list[str], font) -> None:
    x = 10
    for index, label in enumerate(labels):
        width = draw.textlength(label, font=font) + 26
        selected = label == tab
        box = (x, HEADER_H + 8, x + width, HEADER_H + TABS_H)
        draw.rectangle(box, fill=palette["accent"] if selected else palette["panel"],
                       outline=palette["panel"])
        draw.text((x + 13, HEADER_H + 15), label, font=font,
                  fill="#ffffff" if selected else palette["text"])
        x += width + 4


def draw_button(draw, palette: dict[str, str], box, label: str, font, *, primary: bool = False) -> None:
    fill = palette["accent"] if primary else palette["panel"]
    color = "#ffffff" if primary else palette["text"]
    draw.rounded_rectangle(box, radius=4, fill=fill, outline=palette["muted"], width=1)
    width = draw.textlength(label, font=font)
    draw.text((box[0] + (box[2] - box[0] - width) / 2, box[1] + 7), label, font=font, fill=color)


def render(theme: str, out_path: Path) -> Path:
    from PIL import Image, ImageDraw

    from jarvis.core.i18n import get_translator
    from jarvis.interfaces.gui import PALETTES, state_color, trim

    translator = get_translator("ru")
    palette = PALETTES.get(theme, PALETTES["dark"])
    image = Image.new("RGBA", (WIDTH, HEIGHT), palette["bg"])
    draw = ImageDraw.Draw(image)

    font = load_font(15)
    font_bold = load_font(16, bold=True)
    font_small = load_font(13)
    font_mono = load_font(14)

    # ------------------------------------------------------------------ шапка
    draw.rectangle((0, 0, WIDTH, HEADER_H), fill=palette["panel"])
    draw.ellipse((14, 15, 36, 37), fill=state_color("idle"))
    draw.text((46, 17), translator("gui.state.idle"), font=font_bold, fill=palette["text"])
    providers = " · ".join([
        f"{translator('gui.provider.llm')}: {translator('gui.state.not_ready')}",
        f"{translator('gui.provider.stt')}: {translator('gui.state.ready')}",
        f"{translator('gui.provider.tts')}: {translator('gui.state.ready')}",
    ])
    width = draw.textlength(providers, font=font_small)
    draw.text((WIDTH - width - 16, 19), providers, font=font_small, fill=palette["muted"])

    # ----------------------------------------------------------------- вкладки
    draw_tabs(draw, palette, translator("gui.tab.chat"),
              [translator("gui.tab.chat"), translator("gui.tab.skills"), translator("gui.tab.settings"),
               translator("gui.tab.log"), translator("gui.tab.about")], font)

    # ------------------------------------------------------------- лента чата
    chat_top, chat_bottom = HEADER_H + TABS_H + 14, HEIGHT - FOOTER_H - 58
    draw.rectangle((10, chat_top, WIDTH - 10, chat_bottom), fill=palette["entry"])

    lines = [
        ("user", translator("gui.role.you"), "сколько времени"),
        ("jarvis", translator("gui.role.jarvis"), "Сейчас 14:01."),
        ("user", translator("gui.role.you"), "какое сегодня число"),
        ("jarvis", translator("gui.role.jarvis"), "Сегодня 7 октября 2026 года, среда."),
        ("user", translator("gui.role.you"), "выключи компьютер"),
        ("jarvis", translator("gui.role.jarvis"),
         "Выключить компьютер? Подтверждаете?  →  [ Да ]   [ Нет ]"),
        ("user", translator("gui.role.you"), "нет, не надо"),
        ("jarvis", translator("gui.role.jarvis"), "Отменяю."),
    ]
    y = chat_top + 10
    for role, who, text in lines:
        label = f"{who}: "
        draw.text((22, y), label, font=font, fill=palette["muted"])
        offset = draw.textlength(label, font=font)
        color = palette["user"] if role == "user" else palette["jarvis"]
        draw.text((22 + offset, y), trim(text, 96), font=font, fill=color)
        y += 22

    # --------------------------------------------------------- строка ввода
    # порядок как в окне слева направо: [Слушать] [поле ввода] [Озвучивать] [Отправить]
    row_top = HEIGHT - FOOTER_H - 44
    row_bottom = row_top + 30
    listen = translator("gui.chat.listen")
    listen_w = int(draw.textlength(listen, font=font)) + 26
    draw_button(draw, palette, (12, row_top, 12 + listen_w, row_bottom), listen, font)

    send = translator("gui.chat.send")
    send_w = int(draw.textlength(send, font=font)) + 26
    send_left = WIDTH - 12 - send_w
    draw_button(draw, palette, (send_left, row_top, WIDTH - 12, row_bottom), send, font, primary=True)

    speak = "озвучивать"
    speak_w = int(draw.textlength(speak, font=font_small)) + 22
    speak_left = send_left - 8 - speak_w
    draw.rectangle((speak_left, row_top + 8, speak_left + 14, row_top + 22),
                   outline=palette["accent"], width=2)
    draw.line((speak_left + 3, row_top + 14, speak_left + 6, row_top + 18), fill=palette["accent"], width=2)
    draw.line((speak_left + 6, row_top + 18, speak_left + 11, row_top + 9), fill=palette["accent"], width=2)
    draw.text((speak_left + 20, row_top + 7), speak, font=font_small, fill=palette["text"])

    entry_left = 20 + listen_w
    entry_right = speak_left - 10
    draw.rounded_rectangle((entry_left, row_top, entry_right, row_bottom), radius=4,
                           fill=palette["entry"], outline=palette["muted"])
    draw.text((entry_left + 10, row_top + 7), "напишите вопрос и нажмите Enter",
              font=font, fill=palette["muted"])

    # ---------------------------------------------------------------- подвал
    draw.rectangle((0, HEIGHT - FOOTER_H, WIDTH, HEIGHT), fill=palette["panel"])
    draw.text((12, HEIGHT - FOOTER_H + 8), translator("gui.hint"), font=font_small, fill=palette["muted"])

    # --------------------------------------------------- пометка, что это макет
    note = "предпросмотр интерфейса — окно требует tkinter"
    draw.text((WIDTH - draw.textlength(note, font=font_small) - 12, HEIGHT - FOOTER_H + 8),
              note, font=font_small, fill=palette["muted"])

    out_path.parent.mkdir(parents=True, exist_ok=True)
    image.convert("RGB").save(out_path)
    return out_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Нарисовать предпросмотр окна Jarvis")
    parser.add_argument("--theme", default="dark", choices=["dark", "light"])
    parser.add_argument("--out", default=str(PROJECT_ROOT / "docs" / "gui-preview.png"))
    args = parser.parse_args(argv)
    try:
        import PIL  # noqa: F401
    except ImportError:
        print("Нужен Pillow (только для этого инструмента): pip install Pillow", file=sys.stderr)
        return 1
    path = render(args.theme, Path(args.out))
    print(f"Готово: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
