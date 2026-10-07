"""Тесты темы окна: палитры, контраст, шрифты, состояния, переключение.

Главное правило, которое здесь проверяется: **в окне нет цветов и размеров, они
все живут в ``jarvis.interfaces.theme``**. Если кто-то снова напишет ``#2f81f7``
прямо в окне, тест это заметит.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from jarvis.interfaces import gui, theme

PROJECT_ROOT = Path(__file__).resolve().parents[1]

HEX = re.compile(r"#[0-9a-fA-F]{6}\b")

#: ключи, которые обязаны быть в каждой палитре
REQUIRED_KEYS = (
    "bg", "surface", "surface2", "border", "field_border", "text", "muted", "accent",
    "on_accent", "input_bg", "user_bg", "user_text", "assistant_bg", "assistant_text",
    "ok", "warn", "err", "chip_bg", "chip_text", "scroll_trough", "scroll_thumb",
    # совместимость с прежними именами
    "panel", "entry", "user", "jarvis",
)


class PaletteTests(unittest.TestCase):
    def test_palettes_have_all_roles(self):
        for name in ("dark", "light"):
            palette = theme.palette(name)
            for key in REQUIRED_KEYS:
                self.assertIn(key, palette, f"{name}: нет роли {key}")
                self.assertRegex(palette[key], r"^#[0-9a-f]{6}$", f"{name}.{key}")

    def test_aliases_point_to_new_roles(self):
        for name in ("dark", "light"):
            palette = theme.palette(name)
            self.assertEqual(palette["panel"], palette["surface"])
            self.assertEqual(palette["entry"], palette["input_bg"])

    def test_unknown_theme_is_dark(self):
        self.assertEqual(theme.palette("непонятное"), theme.palette("dark"))
        self.assertEqual(theme.resolve(""), "dark")
        self.assertEqual(theme.resolve("DARK"), "dark")

    def test_contrast_of_all_text_pairs(self):
        for name in ("dark", "light"):
            for title, ratio, threshold, ok in theme.audit(name):
                self.assertTrue(ok, f"{name}: {title} — {ratio}:1 при пороге {threshold}:1")

    def test_state_colors_are_visible_and_complete(self):
        states = ("idle", "listening", "thinking", "speaking", "waiting_confirmation", "error")
        for name in ("dark", "light"):
            for state in states:
                color = theme.state_color(state, name)
                self.assertIn(color, theme.STATE_COLORS[name].values())
                for background in ("bg", "surface"):
                    ratio = theme.contrast(color, theme.palette(name)[background])
                    self.assertGreaterEqual(round(ratio, 2), theme.GRAPHIC_CONTRAST_MIN,
                                            f"{name}: состояние {state} на {background} — {ratio}:1")

    def test_unknown_state_falls_back_to_idle(self):
        self.assertEqual(theme.state_color("чепуха"), theme.state_color("idle"))
        self.assertEqual(theme.state_color(None), theme.state_color("idle"))


class ScaleTests(unittest.TestCase):
    def test_spacing_scale_is_the_agreed_one(self):
        self.assertEqual(list(theme.SPACE.values()), [4, 8, 12, 16, 24])
        self.assertEqual(sorted(theme.SPACE.values()), list(theme.SPACE.values()))

    def test_font_scale_covers_all_roles(self):
        self.assertEqual(theme.FONTS["title"]["size"], 21)
        self.assertEqual(theme.FONTS["heading"]["size"], 17)
        self.assertEqual(theme.FONTS["body"]["size"], 15)
        self.assertEqual(theme.FONTS["caption"]["size"], 13)
        self.assertEqual(theme.FONTS["mono"]["size"], 14)
        self.assertLess(theme.FONTS["caption"]["size"], theme.FONTS["body"]["size"])
        self.assertLess(theme.FONTS["body"]["size"], theme.FONTS["title"]["size"])

    def test_font_spec_prefers_installed_family(self):
        available = ["DejaVu Sans", "DejaVu Sans Mono", "Segoe UI"]
        families = theme.resolve_families(available)
        self.assertEqual(families["sans"], "Segoe UI")          # Windows-шрифт важнее
        self.assertEqual(families["mono"], "DejaVu Sans Mono")
        spec = theme.font_spec("heading", families)
        self.assertEqual(spec, ("Segoe UI", 17, "bold"))
        self.assertEqual(theme.font_spec("caption", families), ("Segoe UI", 13))

    def test_font_falls_back_to_tk_default(self):
        families = theme.resolve_families([])
        self.assertEqual(families["sans"], "TkDefaultFont")
        self.assertEqual(families["mono"], "TkFixedFont")
        self.assertEqual(theme.font_spec("body", families), ("TkDefaultFont", 15))

    def test_radius_scale(self):
        self.assertGreater(theme.RADIUS["card"], theme.RADIUS["button"] - 1)
        self.assertGreaterEqual(theme.RADIUS["pill"], 100)


class SystemThemeTests(unittest.TestCase):
    def setUp(self):
        import os

        self._saved = os.environ.pop(theme.THEME_ENV, None)

    def tearDown(self):
        import os

        if self._saved is not None:
            os.environ[theme.THEME_ENV] = self._saved
        else:
            os.environ.pop(theme.THEME_ENV, None)

    def test_environment_overrides_detection(self):
        import os

        os.environ[theme.THEME_ENV] = "light"
        self.assertEqual(theme.system_theme(), "light")
        os.environ[theme.THEME_ENV] = "dark"
        self.assertEqual(theme.system_theme(), "dark")
        self.assertEqual(theme.resolve("system"), "dark")

    def test_detection_never_raises(self):
        import os

        os.environ.pop(theme.THEME_ENV, None)
        self.assertIn(theme.system_theme(), ("dark", "light"))
        self.assertIn(theme.system_theme(probe=False), ("dark",))
        # на любой системе и без всяких программ определение не падает
        original = os.environ.get("PATH")
        os.environ["PATH"] = "/nonexistent"
        try:
            self.assertIn(theme._linux_theme(), (None, "dark", "light"))
            self.assertIn(theme.system_theme(), ("dark", "light"))
        finally:
            if original is not None:
                os.environ["PATH"] = original

    def test_theme_choices_include_system(self):
        self.assertEqual(theme.THEME_CHOICES[0], "system")
        self.assertIn(theme.SYSTEM_THEME, theme.THEME_CHOICES)

    def test_dpi_helpers_do_not_break_on_this_system(self):
        self.assertIsInstance(theme.enable_dpi_awareness(), str)

        class FakeRoot:
            tk = None

            def winfo_fpixels(self, _value):
                return 96.0

        root = FakeRoot()
        self.assertAlmostEqual(theme.tk_scaling(root), 0.75, places=2)

        class NoScreen:
            def winfo_fpixels(self, _value):
                raise RuntimeError("нет экрана")

        self.assertIsInstance(theme.tk_scaling(NoScreen()), float)


class TtkStyleTests(unittest.TestCase):
    def test_style_config_covers_widgets_and_states(self):
        palette = theme.palette("dark")
        config = theme.ttk_style_config(palette)
        for name in ("TFrame", "TLabel", "Muted.TLabel", "TButton", "TEntry", "TCombobox",
                     "TCheckbutton", "Treeview", "Treeview.Heading", "TNotebook", "TNotebook.Tab",
                     "TScrollbar"):
            self.assertIn(name, config, f"нет стиля {name}")
        self.assertEqual(config["TFrame"]["background"], palette["bg"])
        self.assertEqual(config["TEntry"]["fieldbackground"], palette["input_bg"])
        self.assertEqual(config["Treeview"]["background"], palette["surface"])

        states = theme.ttk_style_map(palette)
        for name in ("TButton", "TNotebook.Tab", "Treeview", "TCheckbutton", "TCombobox"):
            self.assertIn(name, states, f"нет состояний для {name}")
        self.assertIn(("selected", palette["accent"]), states["Treeview"]["background"])

    def test_apply_ttk_uses_clam_and_paints_styles(self):
        class FakeStyle:
            def __init__(self):
                self.used = []
                self.settings = {}

            def theme_use(self, name):
                self.used.append(name)

            def configure(self, name, **options):
                self.settings[name] = options

            def map(self, name, **options):
                self.settings.setdefault(name, {}).update(options)

        style = FakeStyle()
        theme.apply_ttk(style, theme.palette("light"), "light")
        self.assertEqual(style.used, ["clam"])
        self.assertEqual(style.settings["TLabel"]["foreground"], theme.palette("light")["text"])
        self.assertIn("Accent.TButton", style.settings)
        self.assertEqual(style.settings["Card.TFrame"]["background"], theme.palette("light")["surface"])

    def test_apply_ttk_survives_stubborn_widgets(self):
        class Broken:
            def theme_use(self, _name):
                raise RuntimeError("нет темы")

            def configure(self, _name, **_options):
                raise RuntimeError("нет опции")

            def map(self, _name, **_options):
                raise RuntimeError("нет опции")

        theme.apply_ttk(Broken(), theme.palette("dark"), "dark")  # не должно бросить


class OneSourceOfTruthTests(unittest.TestCase):
    """Стиль окна должен жить в теме, а не в коде окна."""

    def test_no_colors_in_window_code(self):
        source = (PROJECT_ROOT / "jarvis" / "interfaces" / "gui.py").read_text(encoding="utf-8")
        found = HEX.findall(source)
        self.assertEqual(found, [], f"в gui.py появились цвета: {sorted(set(found))}")

    def test_no_fonts_or_paddings_in_window_code(self):
        source = (PROJECT_ROOT / "jarvis" / "interfaces" / "gui.py").read_text(encoding="utf-8")
        self.assertNotIn("TkDefaultFont", source)
        self.assertNotIn("TkFixedFont", source)
        self.assertIsNone(re.search(r"(?:padx|pady)=\d+", source),
                          "в gui.py появились отступы числом — их место в шкале темы")

    def test_gui_reexports_theme_values(self):
        self.assertIs(gui.PALETTES, theme.PALETTES)
        self.assertEqual(gui.STATE_COLORS, theme.STATE_COLORS["dark"])
        self.assertEqual(gui.SPACE, theme.SPACE)
        self.assertEqual(gui.theme_palette("light"), theme.palette("light"))
        self.assertEqual(gui.state_color("listening", "light"), theme.state_color("listening", "light"))

    def test_theme_labels_are_translated(self):
        from jarvis.core.i18n import get_translator

        t = get_translator("ru")
        self.assertEqual(gui.theme_label(t, "system"), t("gui.theme.system"))
        self.assertEqual(gui.theme_label(t, "dark"), t("gui.theme.dark"))
        for name in ("system", "dark", "light"):
            self.assertEqual(gui.theme_value(t, gui.theme_label(t, name)), name)
        self.assertEqual(gui.theme_value(t, "light"), "light")   # значение из настроек тоже годится
        self.assertEqual(gui.theme_value(t, "чепуха"), "system")

    def test_checker_does_not_repeat_the_palette(self):
        """Проверка контраста берёт цвета и функцию из темы, своей копии не держит."""
        source = (PROJECT_ROOT / "tools" / "contrast_check.py").read_text(encoding="utf-8")
        self.assertEqual(HEX.findall(source), [], "в contrast_check.py снова своя палитра")
        self.assertIn("from jarvis.interfaces import theme", source)

    def test_preview_uses_window_theme_for_adopted_direction(self):
        """Макеты направления A рисуются палитрой из темы окна."""
        source = (PROJECT_ROOT / "tools" / "style_preview.py").read_text(encoding="utf-8")
        self.assertIn("from jarvis.interfaces import theme", source)
        self.assertIn('window_palette("dark")', source)
        self.assertIn("SPACE = dict(window_theme.SPACE)", source)


if __name__ == "__main__":
    unittest.main()
