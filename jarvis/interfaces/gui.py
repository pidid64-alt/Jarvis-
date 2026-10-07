"""Окно Jarvis на Tkinter — тонкая оболочка над локальным API.

Правило простое: **в окне нет логики ассистента**. Оно умеет показывать,
спрашивать и подтверждать; все решения (какой навык выбрать, можно ли выполнять
опасное действие, что записать в настройки) принимает ядро. Общение — только
через ``jarvis.interfaces.client``, поэтому окно можно заменить на другое
(трей, веб, бот), не меняя ядро.

Память: Tkinter тянет за собой только tcl/tk, поэтому окно поднимается на
десятки мегабайт и не требует ни Qt, ни браузера.

Запуск: ``jarvis gui``. Если демон уже работает, окно присоединяется к нему;
если нет — поднимает ядро и API у себя в процессе (чтобы не платить за два
процесса на слабой машине).
"""

from __future__ import annotations

import argparse
import queue
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable

from .. import __version__
from ..core import paths
from ..core.logging_setup import get_logger

log = get_logger("interfaces.gui")

#: сколько ждать между опросами сервера (в миллисекундах)
EVENT_POLL_MS = 400
HISTORY_POLL_MS = 5000
TASK_POLL_MS = 250

#: Стиль окна живёт в одном файле — ``jarvis.interfaces.theme``. Здесь остаются
#: только короткие имена, чтобы окно и инструменты документации читались просто.
from . import theme as theme_module  # noqa: E402  (нужен ниже по файлу)

PALETTES = theme_module.PALETTES
STATE_COLORS = theme_module.STATE_COLORS["dark"]
SPACE = theme_module.SPACE
RADIUS = theme_module.RADIUS


# --------------------------------------------------------------- мелкие helpers


def tkinter_available() -> tuple[bool, str]:
    """Есть ли Tkinter и можно ли вообще открыть окно."""
    try:
        import tkinter  # noqa: F401
    except Exception as exc:  # noqa: BLE001 - модуля может не быть в сборке Python
        return False, ("Не найден модуль tkinter, без него окно не открыть.\n"
                       "  Linux (Arch/CachyOS): sudo pacman -S tk\n"
                       "  Linux (Debian/Ubuntu): sudo apt install python3-tk\n"
                       "  Windows: переустановите Python с галочкой «tcl/tk and IDLE»\n"
                       f"Подробность: {exc}")
    return True, ""


def theme_palette(name: str) -> dict[str, str]:
    """Палитра окна: ``dark``, ``light`` или ``system`` (неизвестное — тёмная)."""
    return theme_module.palette(name)


def state_color(state: str, theme: str = theme_module.DEFAULT_THEME) -> str:
    """Цвет состояния окна; набор цветов разный в тёмной и светлой теме."""
    return theme_module.state_color(state, theme)


def theme_label(t: Callable[..., str], value: Any) -> str:
    """Название темы для человека: ``system`` → «как в системе»."""
    key = str(value or "").strip().lower()
    if key in ("system", "dark", "light"):
        return t(f"gui.theme.{key}")
    for name in ("system", "dark", "light"):  # пришла уже подпись — оставим её
        if str(value) == t(f"gui.theme.{name}"):
            return str(value)
    return t("gui.theme.system")


def theme_value(t: Callable[..., str], label: Any) -> str:
    """Обратно: подпись из списка → значение настройки (``system``, ``dark``, ``light``)."""
    text = str(label or "").strip()
    for name in ("system", "dark", "light"):
        if text.lower() == name or text == t(f"gui.theme.{name}"):
            return name
    return theme_module.SYSTEM_THEME


def trim(text: str, limit: int = 120) -> str:
    text = " ".join((text or "").split())  # переносы и лишние пробелы — в один пробел
    return text if len(text) <= limit else text[: limit - 1] + "…"


def provider_summary(status: dict[str, Any], t: Callable[..., str]) -> str:
    """Короткая строка о провайдерах — её видно в шапке окна."""
    providers = (status or {}).get("providers") or {}
    llm = providers.get("llm") or {}
    stt = providers.get("stt") or {}
    tts = providers.get("tts") or {}
    parts = [
        t("gui.provider.llm") + ": " + (t("gui.state.ready") if llm.get("available") else t("gui.state.not_ready")),
        t("gui.provider.stt") + ": " + (t("gui.state.ready") if stt.get("available") else t("gui.state.not_ready")),
        t("gui.provider.tts") + ": " + (t("gui.state.ready") if tts.get("available") else t("gui.state.not_ready")),
    ]
    return " · ".join(parts)


def permissions_text(skill: dict[str, Any], language: str = "ru") -> str:
    """Права навыка человеческим языком (для страницы «Навыки»)."""
    from ..core.types import Permissions

    lines = Permissions.from_dict(skill.get("permissions") or {}).describe(language)
    return "\n".join("• " + line for line in lines) if lines else "• Только ответы, без доступа к системе"


def permissions_inline(skill: dict[str, Any], language: str = "ru") -> str:
    """Права навыка одной строкой — для колонки в таблице."""
    from ..core.types import Permissions

    lines = Permissions.from_dict(skill.get("permissions") or {}).describe(language)
    return "; ".join(lines) if lines else "—"


def actions_text(skill: dict[str, Any], limit: int = 12) -> str:
    actions = skill.get("actions") or []
    lines = []
    for action in actions[:limit]:
        phrase = (action.get("phrases") or [""])[0]
        mark = " (спросит подтверждение)" if action.get("confirm") else ""
        lines.append(f"• {action.get('description') or action.get('id')} — «{phrase}»{mark}")
    if len(actions) > limit:
        lines.append(f"… и ещё {len(actions) - limit}")
    return "\n".join(lines)


def reply_text(reply: dict[str, Any] | None) -> str:
    if not reply:
        return ""
    text = str(reply.get("text") or reply.get("speech") or "")
    if not reply.get("ok", True) and reply.get("error"):
        return f"{text}\n({reply['error']})"
    return text


class ScrollFrame:
    """Прокручиваемая область — нужна странице настроек на маленьком экране."""

    def __init__(self, parent, palette: dict[str, str]):
        import tkinter as tk
        from tkinter import ttk

        self.outer = tk.Frame(parent, bg=palette["bg"])
        self.canvas = tk.Canvas(self.outer, bg=palette["bg"], highlightthickness=0)
        self.scrollbar = ttk.Scrollbar(self.outer, orient="vertical", command=self.canvas.yview)
        self.inner = tk.Frame(self.canvas, bg=palette["bg"])
        self._window = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.scrollbar.pack(side="right", fill="y")
        self.inner.bind("<Configure>", self._on_inner)
        self.canvas.bind("<Configure>", self._on_canvas)
        for widget in (self.canvas, self.inner):
            widget.bind("<Enter>", lambda *_: self._bind_wheel(True))
            widget.bind("<Leave>", lambda *_: self._bind_wheel(False))

    def _on_inner(self, _event=None) -> None:
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas(self, event=None) -> None:
        if event is not None:
            self.canvas.itemconfigure(self._window, width=event.width)

    def _bind_wheel(self, active: bool) -> None:
        if active:
            self.canvas.bind_all("<MouseWheel>", self._wheel)
            self.canvas.bind_all("<Button-4>", self._wheel)
            self.canvas.bind_all("<Button-5>", self._wheel)
        else:
            for sequence in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
                self.canvas.unbind_all(sequence)

    def _wheel(self, event) -> None:
        if getattr(event, "num", None) == 4:
            self.canvas.yview_scroll(-3, "units")
        elif getattr(event, "num", None) == 5:
            self.canvas.yview_scroll(3, "units")
        else:
            self.canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")

    def pack(self, **kwargs) -> None:
        self.outer.pack(**kwargs)


class GuiApp:
    """Главное окно. Всё, что делает, — показывает данные ядра и отправляет ему ввод."""

    def __init__(self, client, *, translator=None, tray: bool = True,
                 start_minimized: bool = False, embedded: Any = None, debug: bool = False):
        import tkinter as tk
        from tkinter import ttk

        self.client = client
        self.embedded = embedded          # (assistant, api) — если ядро подняли мы сами
        self.debug = debug
        self.tray = None
        self.tray_enabled = tray
        self._hotkey = None
        self._trigger_file = paths.state_dir() / "trigger"
        self._trigger_seen = 0.0
        self._queue: "queue.Queue[tuple[Callable[..., None], Any, BaseException | None]]" = queue.Queue()
        self._inflight = 0                 # сколько запросов сейчас в работе
        self._inflight_lock = threading.Lock()
        self._max_inflight = 4
        self._last_event_seq = 0
        self._last_chat_ts = 0.0
        self._busy = False
        self._pending_id: str | None = None
        self._state_ts = 0.0           # когда мы сами показали состояние
        self._closing = False
        self._skills: dict[str, dict[str, Any]] = {}

        self.root = tk.Tk()
        self.root.title("Jarvis")
        self.root.geometry("880x640")
        self.root.minsize(700, 520)
        theme_module.tk_scaling(self.root)   # 125 % и 150 % в Windows — без мыла

        self._status = self._call(lambda: self.client.status(), default={}) or {}
        self._config = self._call(lambda: self.client.config(), default={}) or {}
        # в настройках может стоять «system»: тогда тему берём у системы
        self.theme = str((self._config.get("config") or {}).get("assistant", {})
                         .get("theme", theme_module.SYSTEM_THEME))
        self.theme_name = theme_module.resolve(self.theme)
        self.palette = theme_module.PALETTES[self.theme_name]
        self.t = translator or self._translator()
        self.fonts = self._resolve_fonts()
        self._themed: list[tuple[Any, dict[str, str]]] = []

        self.style = ttk.Style(self.root)
        self._apply_style()

        self._build_header()
        self._build_tabs()
        self._build_footer()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.bind("<Control-l>", lambda *_: self._listen())
        self.root.bind("<Escape>", lambda *_: self._hide_to_tray())

        self.notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed)
        self._refresh_skills()
        self._refresh_history(initial=True)
        self._refresh_journal()
        self.root.after(120, self._drain)
        self._poll_events()
        self._poll_history()
        self._start_tray()
        self._start_hotkey()
        self._poll_trigger()
        if start_minimized:
            self._hide_to_tray()
        self._set_state("idle", self.t("gui.state.idle"))

    # ------------------------------------------------------------------ helpers
    def _translator(self):
        from ..core.i18n import get_translator

        language = str((self._status or {}).get("language") or "ru")
        return get_translator(language)

    def _call(self, function: Callable[[], Any], *, default: Any = None) -> Any:
        """Синхронный вызов API для момента запуска окна."""
        try:
            return function()
        except Exception:  # noqa: BLE001 - окно должно открыться даже без ядра
            log.warning("ядро не ответило при запуске окна", exc_info=True)
            return default

    def _submit(self, function: Callable[[], Any],
                on_done: Callable[[Any, BaseException | None], None], *,
                poll: bool = False) -> bool:
        """Выполняет запрос в отдельном потоке: окно не должно замирать.

        ``poll=True`` — это фоновое обновление (события, список навыков): если
        ядро не успевает, такой запрос пропускаем, чтобы не копить потоки и
        сокеты. Действия пользователя пропускать нельзя — они всегда уходят.
        False означает, что запрос не отправлен.
        """
        with self._inflight_lock:
            if poll and self._inflight >= self._max_inflight:
                return False
            self._inflight += 1

        def worker() -> None:
            try:
                result = function()
                error: BaseException | None = None
            except BaseException as exc:  # noqa: BLE001 - показываем пользователю
                result, error = None, exc
            finally:
                with self._inflight_lock:
                    self._inflight -= 1
            self._queue.put((on_done, result, error))

        threading.Thread(target=worker, name="jarvis-gui-call", daemon=True).start()
        return True

    def _drain(self) -> None:
        """Забирает готовые ответы в главный поток Tk."""
        while True:
            try:
                on_done, result, error = self._queue.get_nowait()
            except queue.Empty:
                break
            try:
                on_done(result, error)
            except Exception:  # noqa: BLE001
                log.exception("ошибка отображения результата")
        if not self._closing:
            self.root.after(100, self._drain)

    def _resolve_fonts(self) -> dict[str, str]:
        """Подобрать шрифты из установленных: Segoe UI (Windows), Noto/DejaVu (Linux)."""
        try:
            from tkinter import font as tkfont

            return theme_module.resolve_families(tkfont.families(self.root))
        except Exception:  # noqa: BLE001 - в урезанной сборке нет списка шрифтов
            return theme_module.resolve_families(None)

    def font(self, kind: str) -> tuple:
        """Шрифт из шкалы темы: ``self.font("heading")``."""
        return theme_module.font_spec(kind, self.fonts)

    def _colors(self, widget: Any, **mapping: str) -> Any:
        """Виджет и его цвета из палитры — чтобы смена темы шла на лету."""
        self._themed.append((widget, mapping))
        try:
            widget.configure(**{option: self.palette[key] for option, key in mapping.items()})
        except Exception:  # noqa: BLE001 - не все опции есть у всех виджетов
            log.debug("не удалось задать цвета виджета", exc_info=True)
        return widget

    def _apply_style(self) -> None:
        """Единственное место, где окно настраивает ttk. Цвета — только из темы."""
        self.root.configure(bg=self.palette["bg"])
        theme_module.apply_ttk(self.style, self.palette, self.theme_name)
        self._retint()

    def _retint(self) -> None:
        """Перекрасить обычные (не ttk) виджеты после смены темы."""
        for widget, mapping in self._themed:
            try:
                widget.configure(**{option: self.palette[key] for option, key in mapping.items()})
            except Exception:  # noqa: BLE001
                log.debug("виджет не перекрасился", exc_info=True)
        for tag, color_key in getattr(self, "_chat_tags", {}).items():
            try:
                self.chat.tag_configure(tag, foreground=self.palette[color_key])
            except Exception:  # noqa: BLE001
                log.debug("не удалось перекрасить метку чата", exc_info=True)

    def set_theme(self, name: str) -> None:
        """Сменить тему: ``system``, ``dark`` или ``light``."""
        self.theme = str(name or theme_module.SYSTEM_THEME)
        resolved = theme_module.resolve(self.theme)
        if resolved != self.theme_name:
            self.theme_name = resolved
            self.palette = theme_module.PALETTES[resolved]
        self._apply_style()
        log.info("тема окна: %s (показана %s)", self.theme, resolved)

    # -------------------------------------------------------------------- вёрстка
    def _build_header(self) -> None:
        import tkinter as tk
        from tkinter import ttk

        palette = self.palette
        self._header = self._colors(tk.Frame(self.root, height=56), bg="panel")
        self._header.pack(side="top", fill="x")
        self._header.pack_propagate(False)

        self.state_canvas = self._colors(tk.Canvas(self._header, width=22, height=22,
                                                   highlightthickness=0), bg="panel")
        self.state_canvas.pack(side="left", padx=(SPACE["l"], SPACE["s"]),
                               pady=SPACE["l"] + SPACE["xs"])
        self.state_dot = self.state_canvas.create_oval(3, 3, 19, 19,
                                                       fill=state_color("idle", self.theme_name), outline="")

        self.state_label = self._colors(tk.Label(self._header, text=self.t("gui.state.idle"),
                                                 font=self.font("heading")), bg="panel", fg="text")
        self.state_label.pack(side="left")

        self.provider_label = ttk.Label(self._header, text=provider_summary(self._status, self.t),
                                        style="Muted.TLabel")
        self.provider_label.pack(side="right", padx=SPACE["l"])

    def _build_tabs(self) -> None:
        from tkinter import ttk

        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(side="top", fill="both", expand=True,
                           padx=SPACE["m"], pady=(SPACE["s"], 0))
        self.tab_chat = ttk.Frame(self.notebook)
        self.tab_skills = ttk.Frame(self.notebook)
        self.tab_settings = ttk.Frame(self.notebook)
        self.tab_log = ttk.Frame(self.notebook)
        self.tab_about = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_chat, text=self.t("gui.tab.chat"))
        self.notebook.add(self.tab_skills, text=self.t("gui.tab.skills"))
        self.notebook.add(self.tab_settings, text=self.t("gui.tab.settings"))
        self.notebook.add(self.tab_log, text=self.t("gui.tab.log"))
        self.notebook.add(self.tab_about, text=self.t("gui.tab.about"))

        self._build_chat_tab()
        self._build_skills_tab()
        self._build_settings_tab()
        self._build_log_tab()
        self._build_about_tab()

    def _build_chat_tab(self) -> None:
        import tkinter as tk
        from tkinter import ttk

        palette = self.palette
        frame = self.tab_chat
        chat_frame = self._colors(tk.Frame(frame), bg="bg")
        chat_frame.pack(side="top", fill="both", expand=True, padx=SPACE["s"], pady=SPACE["s"])

        self.chat = self._colors(tk.Text(chat_frame, wrap="word", state="disabled", height=18,
                                         relief="flat", padx=SPACE["m"], pady=SPACE["s"],
                                         font=self.font("body")), bg="entry", fg="text")
        scrollbar = ttk.Scrollbar(chat_frame, orient="vertical", command=self.chat.yview)
        self.chat.configure(yscrollcommand=scrollbar.set)
        self.chat.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        #: какие метки чата каким цветом красить — пригодится при смене темы
        self._chat_tags = {"user": "user", "jarvis": "jarvis", "system": "muted"}
        self._retint()

        bottom = self._colors(tk.Frame(frame), bg="bg")
        bottom.pack(side="bottom", fill="x", padx=SPACE["s"], pady=(0, SPACE["s"]))

        self.mic_button = ttk.Button(bottom, text=self.t("gui.chat.listen"), command=self._listen)
        self.mic_button.pack(side="left")

        self.entry = ttk.Entry(bottom, font=self.font("body"))
        self.entry.pack(side="left", fill="x", expand=True, padx=SPACE["s"])
        self.entry.bind("<Return>", lambda *_: self._send())

        self.send_button = ttk.Button(bottom, text=self.t("gui.chat.send"),
                                      command=self._send, style="Accent.TButton")
        self.send_button.pack(side="right")

        self.speak_var = tk.BooleanVar(value=bool(self._config.get("config", {})
                                                  .get("voice", {}).get("enabled", True)))
        ttk.Checkbutton(bottom, text=self.t("gui.chat.speak"),
                        variable=self.speak_var).pack(side="right", padx=SPACE["s"])

    def _build_skills_tab(self) -> None:
        import tkinter as tk
        from tkinter import ttk

        palette = self.palette
        frame = self.tab_skills

        top = self._colors(tk.Frame(frame), bg="bg")
        top.pack(side="top", fill="both", expand=True, padx=SPACE["s"], pady=SPACE["s"])

        columns = ("name", "state", "rights")
        self.skills_tree = ttk.Treeview(top, columns=columns, show="headings", height=12)
        self.skills_tree.heading("name", text=self.t("gui.skills.name"))
        self.skills_tree.heading("state", text=self.t("gui.skills.state"))
        self.skills_tree.heading("rights", text=self.t("gui.skills.rights"))
        self.skills_tree.column("name", width=250, anchor="w")
        self.skills_tree.column("state", width=90, anchor="center")
        self.skills_tree.column("rights", width=330, anchor="w")
        self.skills_tree.pack(side="left", fill="both", expand=True)
        self.skills_tree.bind("<<TreeviewSelect>>", lambda *_: self._show_skill_details())

        scrollbar = ttk.Scrollbar(top, orient="vertical", command=self.skills_tree.yview)
        self.skills_tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")

        details = self._colors(tk.Frame(frame), bg="bg")
        details.pack(side="bottom", fill="x", padx=SPACE["s"], pady=(0, SPACE["s"]))
        self.skill_details = self._colors(tk.Text(details, height=7, wrap="word", state="disabled",
                                                  relief="flat", padx=SPACE["m"], pady=SPACE["s"],
                                                  font=self.font("body")), bg="entry", fg="text")
        self.skill_details.pack(side="top", fill="x")

        buttons = self._colors(tk.Frame(frame), bg="bg")
        buttons.pack(side="bottom", fill="x", padx=SPACE["s"], pady=SPACE["s"])
        ttk.Button(buttons, text=self.t("gui.skills.enable"), command=lambda: self._toggle_skill(True)).pack(side="left")
        ttk.Button(buttons, text=self.t("gui.skills.disable"), command=lambda: self._toggle_skill(False)).pack(side="left", padx=SPACE["s"])
        ttk.Button(buttons, text=self.t("gui.refresh"), command=self._refresh_skills).pack(side="left")
        self.skills_hint = ttk.Label(buttons, text="", style="Muted.TLabel")
        self.skills_hint.pack(side="right")

    def _build_settings_tab(self) -> None:
        import tkinter as tk
        from tkinter import ttk

        palette = self.palette
        self.settings_vars: dict[str, Any] = {}
        self.settings_kinds: dict[str, str] = {}
        self.settings_original: dict[str, Any] = {}
        config = (self._config.get("config") or {})

        scroller = ScrollFrame(self.tab_settings, palette)
        scroller.pack(side="top", fill="both", expand=True)
        body = scroller.inner

        def section(title: str) -> tk.Frame:
            block = self._colors(tk.Frame(body), bg="bg")
            block.pack(side="top", fill="x", padx=SPACE["m"], pady=(SPACE["m"], 0))
            ttk.Label(block, text=title, style="Heading.TLabel",
                      font=self.font("heading")).pack(side="top", anchor="w")
            inner = self._colors(tk.Frame(block), bg="bg")
            inner.pack(side="top", fill="x", pady=(SPACE["xs"], 0))
            return inner

        def field(parent, label: str, key: str, value: str, note: str = "") -> None:
            row = self._colors(tk.Frame(parent), bg="bg")
            row.pack(side="top", fill="x", pady=SPACE["xs"] // 2)
            ttk.Label(row, text=label, width=26).pack(side="left")
            var = tk.StringVar(value=str(value))
            entry = ttk.Entry(row, textvariable=var, width=46)
            entry.pack(side="left")
            self.settings_vars[key] = var
            self.settings_kinds[key] = "text"
            self.settings_original[key] = str(value)
            if note:
                ttk.Label(row, text=note, style="Muted.TLabel").pack(side="left", padx=SPACE["s"])

        def check(parent, label: str, key: str, value: bool) -> None:
            var = tk.BooleanVar(value=bool(value))
            ttk.Checkbutton(parent, text=label, variable=var).pack(side="top", anchor="w")
            self.settings_vars[key] = var
            self.settings_kinds[key] = "bool"
            self.settings_original[key] = bool(value)

        # --- модель
        llm = config.get("llm") or {}
        block = section(self.t("gui.settings.model"))
        field(block, "base_url", "llm.base_url", llm.get("base_url", ""))
        field(block, "model", "llm.model", llm.get("model", ""))
        check(block, self.t("gui.settings.llm_enabled"), "llm.enabled", llm.get("enabled", True))

        env_names = {item["name"]: item for item in (self._config.get("env") or {}).get("names", [])}
        env_entry = env_names.get("JARVIS_LLM_KEY") or {"set": False, "masked": ""}
        key_row = self._colors(tk.Frame(block), bg="bg")
        key_row.pack(side="top", fill="x", pady=SPACE["xs"] // 2)
        state = (self.t("gui.settings.key_set", masked=env_entry.get("masked") or "…")
                 if env_entry.get("set") else self.t("gui.settings.key_missing"))
        ttk.Label(key_row, text=self.t("gui.settings.key"), width=26).pack(side="left")
        ttk.Label(key_row, text=state, style="Muted.TLabel").pack(side="left")
        self.secret_var = tk.StringVar(value="")
        secret_entry = ttk.Entry(key_row, textvariable=self.secret_var, width=32, show="•")
        secret_entry.pack(side="left", padx=SPACE["s"])
        ttk.Button(key_row, text=self.t("gui.settings.key_save"),
                   command=self._save_secret).pack(side="left")
        ttk.Label(block, text=self.t("gui.settings.key_note"), style="Muted.TLabel").pack(side="top", anchor="w")

        # --- голос
        voice = config.get("voice") or {}
        wake = voice.get("wakeword") or {}
        block = section(self.t("gui.settings.voice"))
        check(block, self.t("gui.settings.voice_enabled"), "voice.enabled", voice.get("enabled", True))
        row = self._colors(tk.Frame(block), bg="bg")
        row.pack(side="top", fill="x", pady=SPACE["xs"] // 2)
        ttk.Label(row, text=self.t("gui.settings.language"), width=26).pack(side="left")
        languages = self._call(lambda: self.client.get("/languages").get("languages", ["ru"]), default=["ru"])
        self.language_var = tk.StringVar(value=str(config.get("assistant", {}).get("language", "ru")))
        ttk.Combobox(row, textvariable=self.language_var, values=languages, width=10,
                     state="readonly").pack(side="left")
        self.settings_vars["assistant.language"] = self.language_var
        self.settings_kinds["assistant.language"] = "choice"
        self.settings_original["assistant.language"] = self.language_var.get()
        field(block, self.t("gui.settings.tts_voice"), "tts.voice", (config.get("tts") or {}).get("voice", ""))
        check(block, self.t("gui.settings.wakeword"), "voice.wakeword.enabled", wake.get("enabled", False))

        # --- управление
        hotkey = config.get("hotkey") or {}
        block = section(self.t("gui.settings.hotkey"))
        field(block, self.t("gui.settings.hotkey_spec"), "hotkey.spec", hotkey.get("spec", "Super+J"))
        check(block, self.t("gui.settings.hotkey_enabled"), "hotkey.enabled", hotkey.get("enabled", True))

        # --- интерфейс
        ui = config.get("ui") or {}
        block = section(self.t("gui.settings.interface"))
        row = self._colors(tk.Frame(block), bg="bg")
        row.pack(side="top", fill="x", pady=SPACE["xs"] // 2)
        ttk.Label(row, text=self.t("gui.settings.theme"), width=26).pack(side="left")
        self.theme_var = tk.StringVar(value=theme_label(self.t, self.theme))
        ttk.Combobox(row, textvariable=self.theme_var, width=16, state="readonly",
                     values=[theme_label(self.t, name) for name in theme_module.THEME_CHOICES]
                     ).pack(side="left")
        ttk.Label(row, text=self.t("gui.settings.theme_note"), style="Muted.TLabel").pack(
            side="left", padx=SPACE["s"])
        self.settings_vars["assistant.theme"] = self.theme_var
        self.settings_kinds["assistant.theme"] = "theme"
        self.settings_original["assistant.theme"] = self.theme  # сравниваем имена, не подписи
        check(block, self.t("gui.settings.close_to_tray"), "ui.close_to_tray", ui.get("close_to_tray", True))
        check(block, self.t("gui.settings.start_minimized"), "ui.start_minimized", ui.get("start_minimized", False))

        # --- права
        permissions = config.get("permissions") or {}
        block = section(self.t("gui.settings.permissions"))
        row = self._colors(tk.Frame(block), bg="bg")
        row.pack(side="top", fill="x", pady=SPACE["xs"] // 2)
        ttk.Label(row, text=self.t("gui.settings.mode"), width=26).pack(side="left")
        self.mode_var = tk.StringVar(value=str(permissions.get("mode", "restricted")))
        ttk.Combobox(row, textvariable=self.mode_var, values=["restricted", "normal"], width=14,
                     state="readonly").pack(side="left")
        self.settings_vars["permissions.mode"] = self.mode_var
        self.settings_kinds["permissions.mode"] = "choice"
        self.settings_original["permissions.mode"] = self.mode_var.get()
        ttk.Label(block, text=self.t("gui.settings.mode_note"), style="Muted.TLabel").pack(side="top", anchor="w")

        footer = self._colors(tk.Frame(body), bg="bg")
        footer.pack(side="top", fill="x", padx=SPACE["m"], pady=SPACE["l"])
        ttk.Button(footer, text=self.t("gui.settings.save"),
                   style="Accent.TButton", command=self._save_settings).pack(side="left")
        self.settings_status = ttk.Label(footer, text="", style="Muted.TLabel")
        self.settings_status.pack(side="left", padx=SPACE["s"])

    def _build_log_tab(self) -> None:
        import tkinter as tk
        from tkinter import ttk

        palette = self.palette
        frame = self.tab_log
        top = self._colors(tk.Frame(frame), bg="bg")
        top.pack(side="top", fill="x", padx=SPACE["s"], pady=SPACE["s"])
        ttk.Label(top, text=self.t("gui.log.level")).pack(side="left")
        self.log_level = tk.StringVar(value="all")
        ttk.Combobox(top, textvariable=self.log_level, values=["all", "info", "warning", "error"],
                     width=10, state="readonly").pack(side="left", padx=SPACE["s"])
        ttk.Label(top, text=self.t("gui.log.search")).pack(side="left", padx=(SPACE["m"], SPACE["xs"]))
        self.log_search = tk.StringVar(value="")
        ttk.Entry(top, textvariable=self.log_search, width=24).pack(side="left")
        ttk.Button(top, text=self.t("gui.refresh"), command=self._refresh_journal).pack(side="left", padx=SPACE["s"])
        ttk.Button(top, text=self.t("gui.log.copy"), command=self._copy_report).pack(side="left")

        self.log_text = self._colors(tk.Text(frame, wrap="word", state="disabled", relief="flat",
                                             padx=SPACE["m"], pady=SPACE["s"], font=self.font("mono")),
                                     bg="entry", fg="text")
        self.log_text.pack(side="top", fill="both", expand=True, padx=SPACE["s"], pady=(0, SPACE["s"]))
        self.log_hint = ttk.Label(frame, text=self.t("gui.log.hint"), style="Muted.TLabel")
        self.log_hint.pack(side="bottom", anchor="w", padx=SPACE["s"], pady=(0, SPACE["s"]))

    def _build_about_tab(self) -> None:
        import tkinter as tk
        from tkinter import ttk

        palette = self.palette
        text = (
            f"{self.t('gui.about.title')}: Jarvis {__version__}\n\n"
            f"{self.t('gui.about.core')}: {self.client.url}\n"
            f"{self.t('gui.about.config')}: {paths.config_path()}\n"
            f"{self.t('gui.about.data')}: {paths.state_dir()}\n"
            f"{self.t('gui.about.logs')}: {paths.journal_file()}\n\n"
            f"{self.t('gui.about.licenses')}\n"
            "• Piper (rhasspy/piper) — MIT: синтез речи\n"
            "• whisper.cpp (ggerganov) — MIT: распознавание речи\n"
            "• openWakeWord (dscripay) — Apache-2.0: слово-активатор\n"
            "• pystray, Pillow — LGPL/HPND: значок в трее\n\n"
            f"{self.t('gui.about.privacy')}"
        )
        widget = self._colors(tk.Text(self.tab_about, wrap="word", height=18, relief="flat",
                                      padx=SPACE["m"], pady=SPACE["m"], font=self.font("body")),
                              bg="entry", fg="text")
        widget.insert("1.0", text)
        widget.configure(state="disabled")
        widget.pack(side="top", fill="both", expand=True, padx=SPACE["m"], pady=SPACE["m"])
        self.about_text = widget
        row = self._colors(tk.Frame(self.tab_about), bg="bg")
        row.pack(side="bottom", fill="x", padx=SPACE["m"], pady=(0, SPACE["m"]))
        ttk.Button(row, text=self.t("gui.about.open_config"),
                   command=lambda: self._open_path(paths.config_dir())).pack(side="left")
        ttk.Button(row, text=self.t("gui.about.copy_info"),
                   command=lambda: self._copy(text)).pack(side="left", padx=SPACE["s"])

    def _build_footer(self) -> None:
        import tkinter as tk
        from tkinter import ttk

        self._footer = self._colors(tk.Frame(self.root, height=32), bg="panel")
        self._footer.pack(side="bottom", fill="x")
        self._footer.pack_propagate(False)
        self.hint_label = ttk.Label(self._footer, text=self.t("gui.hint"), style="Muted.TLabel")
        self.hint_label.pack(side="left", padx=SPACE["m"])

    # ------------------------------------------------------------------- чат
    def _append_chat(self, role: str, text: str) -> None:
        if not text:
            return
        prefix = {"user": self.t("gui.role.you"), "jarvis": self.t("gui.role.jarvis")}.get(role, "")
        self.chat.configure(state="normal")
        if prefix:
            self.chat.insert("end", f"{prefix}: ", (role,))
        self.chat.insert("end", f"{text}\n\n", (role if role in ("user", "jarvis") else "system",))
        self.chat.see("end")
        self.chat.configure(state="disabled")

    def _send(self) -> None:
        text = self.entry.get().strip()
        if not text or self._busy:
            return
        self.entry.delete(0, "end")
        self._busy = True
        self._set_state("thinking", self.t("gui.state.thinking"))
        speak = bool(getattr(self, "speak_var", None) and self.speak_var.get())

        def done(answer, error):
            self._busy = False
            if error is not None:
                self.entry.insert(0, text)  # текст не потеряется, если ядро не ответило
                self._show_error(error)
                return
            self._handle_answer(answer)

        # Лента чата рисуется из истории ядра: так реплики не задваиваются,
        # а окно показывает ровно то, что записано в журнале диалога.
        self._submit(lambda: self.client.ask(text, speak=speak), done)

    def _listen(self) -> None:
        if self._busy:
            return
        self._busy = True
        self._set_state("listening", self.t("gui.state.listening"))
        speak = bool(getattr(self, "speak_var", None) and self.speak_var.get())

        def done(answer, error):
            self._busy = False
            if error is not None:
                self._show_error(error)
                return
            self._handle_answer(answer)

        self._submit(lambda: self.client.voice(speak=speak), done)

    def _handle_answer(self, answer: dict[str, Any] | None) -> None:
        answer = answer or {}
        state = answer.get("state")
        if state == "confirmation_required":
            self._ask_confirmation(str(answer.get("pending_id")), str(answer.get("question") or ""))
        elif state == "done":
            self._render_reply(answer.get("reply"))
        elif state in ("working", "timeout"):
            self._watch_task(str(answer.get("pending_id") or self._pending_id))
        else:
            self._set_state("idle", self.t("gui.state.idle"))

    def _ask_confirmation(self, pending_id: str, question: str) -> None:
        from tkinter import messagebox

        self._pending_id = pending_id
        self._set_state("waiting_confirmation", self.t("gui.state.waiting_confirmation"))
        approved = messagebox.askyesno("Jarvis", question or self.t("gui.confirm.default"))
        self._set_state("thinking", self.t("gui.state.thinking"))

        def done(answer, error):
            if error is not None:
                self._show_error(error)
                return
            state = (answer or {}).get("state")
            if state == "done":
                self._render_reply((answer or {}).get("reply"))
            else:
                self._watch_task(pending_id)

        self._submit(lambda: self.client.confirm(pending_id, approved), done)

    def _watch_task(self, pending_id: str | None) -> None:
        if not pending_id:
            self._set_state("idle", self.t("gui.state.idle"))
            return
        self._pending_id = pending_id

        def poll() -> None:
            def done(answer, error):
                if error is not None:
                    self._show_error(error)
                    return
                state = (answer or {}).get("state")
                if state == "done":
                    self._pending_id = None
                    self._render_reply((answer or {}).get("reply"))
                elif state == "confirmation_required":
                    self._ask_confirmation(str((answer or {}).get("pending_id")), str((answer or {}).get("question") or ""))
                else:
                    self.root.after(TASK_POLL_MS, poll)

            self._submit(lambda: self.client.result(pending_id), done)

        self.root.after(TASK_POLL_MS, poll)

    def _render_reply(self, reply: dict[str, Any] | None) -> None:
        self._state_ts = time.time()  # наш ответ свежее любых прошлых событий
        state = "error" if reply and not reply.get("ok", True) else "idle"
        self._set_state(state, self.t("gui.state.error") if state == "error" else self.t("gui.state.idle"))
        self._refresh_history()

    def _show_error(self, error: BaseException) -> None:
        self._append_chat("system", self.t("gui.error.core", reason=str(error)))
        self._set_state("error", self.t("gui.state.error"))

    # ---------------------------------------------------------------- страницы
    def _on_tab_changed(self, _event=None) -> None:
        try:
            current = self.notebook.index(self.notebook.select())
        except Exception:  # noqa: BLE001
            return
        if current == 1:
            self._refresh_skills()
        elif current == 3:
            self._refresh_journal()

    def _set_state(self, state: str, label: str | None = None) -> None:
        self._state_ts = max(self._state_ts, time.time())
        color = state_color(state, self.theme_name)
        try:
            self.state_canvas.itemconfigure(self.state_dot, fill=color)
            if label:
                self.state_label.configure(text=label)
        except Exception:  # noqa: BLE001 - окно могло уже закрыться
            log.debug("не удалось обновить индикатор", exc_info=True)

    def _refresh_skills(self, *_args) -> None:
        def done(skills, error):
            if error is not None:
                self._show_error(error)
                return
            self._skills = {item["id"]: item for item in (skills or [])}
            self.skills_tree.delete(*self.skills_tree.get_children())
            for skill in sorted(self._skills.values(),
                               key=lambda item: (not item.get("enabled"), item["name"])):
                self.skills_tree.insert(
                    "", "end", iid=skill["id"],
                    values=(skill["name"],
                            self.t("gui.skills.on") if skill.get("enabled") else self.t("gui.skills.off"),
                            trim(permissions_inline(skill, self.t.language), 70)))
            self.skills_hint.configure(text=self.t("gui.skills.total", count=len(self._skills)))

        self._submit(lambda: self.client.skills(), done, poll=True)

    def _selected_skill(self) -> dict[str, Any] | None:
        selection = self.skills_tree.selection()
        return self._skills.get(selection[0]) if selection else None

    def _show_skill_details(self) -> None:
        skill = self._selected_skill()
        if not skill:
            return
        origin = self.t("gui.skills.builtin") if skill.get("builtin") else self.t("gui.skills.migrated")
        text = (f"{skill['name']} ({skill['id']}, {origin})\n"
                f"{skill.get('description', '')}\n\n"
                f"{self.t('gui.skills.rights')}:\n{permissions_text(skill, self.t.language)}\n\n"
                f"{self.t('gui.skills.actions')}:\n{actions_text(skill)}")
        self.skill_details.configure(state="normal")
        self.skill_details.delete("1.0", "end")
        self.skill_details.insert("1.0", text)
        self.skill_details.configure(state="disabled")

    def _toggle_skill(self, enabled: bool) -> None:
        skill = self._selected_skill()
        if not skill:
            self.skills_hint.configure(text=self.t("gui.skills.pick"))
            return

        def done(_answer, error):
            if error is not None:
                self._show_error(error)
                return
            self._refresh_skills()

        self._submit(lambda: self.client.toggle_skill(skill["id"], enabled), done)

    def _save_settings(self) -> None:
        changes: list[tuple[str, Any]] = []
        for key, var in self.settings_vars.items():
            kind = self.settings_kinds.get(key)
            value: Any = bool(var.get()) if kind == "bool" else str(var.get())
            if kind == "theme":
                value = theme_value(self.t, value)  # в списке — подписи, в настройки — имена
            if value == self.settings_original.get(key):
                continue  # пишем только изменённое, лишний раз файл не трогаем
            changes.append((key, value))
        if not changes:
            self.settings_status.configure(text=self.t("gui.settings.unchanged"))
            return
        self.settings_status.configure(text=self.t("gui.settings.saving"))

        def done(result, error):
            if error is not None:
                self.settings_status.configure(text=self.t("gui.settings.save_failed", reason=str(error)))
                return
            applied = (result or {}).get("applied", 0)
            failed = (result or {}).get("failed") or []
            for key, value in changes:
                self.settings_original[key] = value
                if key == "assistant.theme":
                    self.theme_var.set(theme_label(self.t, str(value)))
            if failed:
                self.settings_status.configure(
                    text=self.t("gui.settings.save_partial", applied=applied, failed=", ".join(failed[:3])))
            else:
                self.settings_status.configure(text=self.t("gui.settings.saved", applied=applied))
            self._reload_ui_state()

        def work() -> dict[str, Any]:
            applied, failed = 0, []
            for key, value in changes:
                try:
                    self.client.set_config(key, value)
                    applied += 1
                except Exception as exc:  # noqa: BLE001 - покажем, что именно не сохранилось
                    failed.append(f"{key}: {exc}")
            return {"applied": applied, "failed": failed}

        self._submit(work, done)

    def _save_secret(self) -> None:
        value = self.secret_var.get().strip()
        if not value:
            self.settings_status.configure(text=self.t("gui.settings.key_empty"))
            return
        self.settings_status.configure(text=self.t("gui.settings.key_saving"))

        def done(result, error):
            if error is not None:
                self.settings_status.configure(text=self.t("gui.settings.save_failed", reason=str(error)))
                return
            self.secret_var.set("")
            self.settings_status.configure(text=self.t("gui.settings.key_saved", masked=(result or {}).get("masked", "…")))
            self._config = self._call(lambda: self.client.config(), default=self._config) or self._config

        self._submit(lambda: self.client.save_secret("JARVIS_LLM_KEY", value), done)

    def _reload_ui_state(self) -> None:
        self._config = self._call(lambda: self.client.config(), default=self._config) or self._config
        theme = str((self._config.get("config") or {}).get("assistant", {}).get("theme", self.theme))
        if theme != self.theme:
            self.set_theme(theme)
        self._status = self._call(lambda: self.client.status(), default=self._status) or self._status
        self.provider_label.configure(text=provider_summary(self._status, self.t))

    def _refresh_journal(self, *_args) -> None:
        level = None if getattr(self, "log_level", None) is None or self.log_level.get() == "all" \
            else self.log_level.get()
        search = self.log_search.get() if hasattr(self, "log_search") else ""

        def done(payload, error):
            if error is not None:
                self._show_error(error)
                return
            lines = (payload or {}).get("lines") or []
            self.log_text.configure(state="normal")
            self.log_text.delete("1.0", "end")
            self.log_text.insert("1.0", "\n".join(lines) if lines else self.t("gui.log.empty"))
            self.log_text.see("end")
            self.log_text.configure(state="disabled")

        self._submit(lambda: self.client.journal(limit=300, level=level, search=search), done,
                     poll=False)  # страницу журнала обновляем по-настоящему

    def _copy_report(self) -> None:
        def done(report, error):
            if error is not None:
                self.log_hint.configure(text=self.t("gui.log.copy_failed", reason=str(error)))
                return
            self._copy(report, hint=self.t("gui.log.copied"))

        self._submit(lambda: self.client.report(limit=300), done)

    def _copy(self, text: str, *, hint: str | None = None) -> None:
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        if hint:
            if hasattr(self, "log_hint"):
                self.log_hint.configure(text=hint)
        else:
            self.hint_label.configure(text=self.t("gui.copied"))

    def _open_path(self, path: Path) -> None:
        try:
            from ..platform import get_platform

            get_platform().open_url(path.as_uri())
        except Exception:  # noqa: BLE001 - приложение может не найти файловый менеджер
            log.debug("не удалось открыть %s", path, exc_info=True)

    # --------------------------------------------------- обновления из ядра
    def _poll_events(self) -> None:
        def done(events, error):
            if error is None:
                for event in events or []:
                    self._last_event_seq = max(self._last_event_seq, int(event.get("seq", 0)))
                    if event.get("type") == "state" and float(event.get("ts", 0)) > self._state_ts:
                        # событие старше показанного ответа не перебивает индикатор
                        self._set_state(str(event.get("state", "idle")),
                                        self.t(f"gui.state.{event.get('state', 'idle')}"))
                    if event.get("type") == "skills_changed" and event.get("skill"):
                        pass  # список обновится при следующем открытии страницы
            if not self._closing:
                self.root.after(EVENT_POLL_MS, self._poll_events)

        self._submit(lambda: self.client.events(since=self._last_event_seq), done, poll=True)

    def _poll_history(self) -> None:
        self._refresh_history()
        if not self._closing:
            self.root.after(HISTORY_POLL_MS, self._poll_history)

    def _refresh_history(self, *, initial: bool = False) -> None:
        def done(records, error):
            if error is not None:
                return
            for record in records or []:
                ts = float(record.get("ts", 0))
                if ts <= self._last_chat_ts:
                    continue
                self._last_chat_ts = ts
                role = "user" if record.get("role") == "user" else "jarvis"
                self._append_chat(role, str(record.get("text", "")))
            if initial:
                self._append_chat("system", self.t("gui.chat.greeting"))

        self._submit(lambda: self.client.history(limit=30), done, poll=True)

    # ------------------------------------------------------------------- трей
    def _start_tray(self) -> None:
        if not self.tray_enabled:
            return
        try:
            from .tray import create_tray, tray_available
        except Exception:  # noqa: BLE001
            return
        ready, reason = tray_available()
        if not ready:
            self.hint_label.configure(text=self.t("gui.tray.unavailable", reason=reason))
            return
        self.tray = create_tray(title=self.t("gui.tray.show"), tooltip="Jarvis",
                                on_show=lambda: self._queue.put((lambda *_: self._show_window(), None, None)),
                                on_listen=lambda: self._queue.put((lambda *_: self._listen(), None, None)),
                                on_quit=lambda: self._queue.put((lambda *_: self._quit(), None, None)),
                                listen_title=self.t("gui.tray.listen"),
                                quit_title=self.t("gui.tray.quit"),
                                color=self.palette["accent"])
        if self.tray is None:
            self.hint_label.configure(text=self.t("gui.tray.unavailable", reason="pystray"))
            return
        threading.Thread(target=self.tray.run, name="jarvis-tray", daemon=True).start()

    def _start_hotkey(self) -> None:
        """Горячая клавиша нужна только тогда, когда ядро живёт внутри окна.

        Если работает демон, клавишу держит он: окно в это не вмешивается,
        чтобы не занять сочетание дважды.
        """
        if self.embedded is None:
            return
        if not bool((self._config.get("config") or {}).get("hotkey", {}).get("enabled", True)):
            return
        spec = str((self._config.get("config") or {}).get("hotkey", {}).get("spec", "Super+J"))
        from ..platform import get_platform, platform_name

        if platform_name() == "windows":
            try:
                from ..platform.windows import WindowsHotkeyListener

                listener = WindowsHotkeyListener(spec, lambda: self._queue.put(
                    (lambda *_: (self._show_window(), self._listen()), None, None)))
                if listener.start():
                    self._hotkey = listener
                    return
            except Exception:  # noqa: BLE001 - горячая клавиша не критична
                log.warning("не удалось включить горячую клавишу", exc_info=True)
        ok, hint = get_platform().setup_hotkey(spec, f"{sys.executable} -m jarvis trigger")
        if ok:
            log.info("горячая клавиша %s назначена", spec)
        else:
            log.info("горячая клавиша не назначена: %s", hint)

    def _poll_trigger(self) -> None:
        """Следит за файлом-триггером: так приходит нажатие горячей клавиши.

        Дешёвая проверка времени изменения — раз в секунду, без чтения файла.
        """
        if self.embedded is not None:
            try:
                stamp = self._trigger_file.stat().st_mtime if self._trigger_file.exists() else 0.0
            except OSError:
                stamp = 0.0
            if stamp > self._trigger_seen:
                self._trigger_seen = stamp
                if time.time() - stamp <= 5.0:  # старый триггер не считаем новым
                    self._show_window()
                    self._listen()
        if not self._closing:
            self.root.after(1000, self._poll_trigger)

    def _hide_to_tray(self) -> None:
        if self.tray is None:
            self._quit()
            return
        self.root.withdraw()
        self.hint_label.configure(text=self.t("gui.tray.hidden"))

    def _show_window(self) -> None:
        self.root.deiconify()
        self.root.lift()
        try:
            self.root.focus_force()
        except Exception:  # noqa: BLE001 - на некоторых столах фокус запрещён
            pass

    def _on_close(self) -> None:
        close_to_tray = bool((self._config.get("config") or {}).get("ui", {}).get("close_to_tray", True))
        if self.tray is not None and close_to_tray:
            self._hide_to_tray()
            return
        self._quit()

    # ------------------------------------------------------------------ запуск
    def _quit(self) -> None:
        if self._closing:
            return
        self._closing = True
        if self._hotkey is not None:
            try:
                self._hotkey.stop()
            except Exception:  # noqa: BLE001
                log.debug("слушатель клавиши не остановился штатно", exc_info=True)
            self._hotkey = None
        if self.tray is not None:
            try:
                self.tray.stop()
            except Exception:  # noqa: BLE001
                log.debug("трей не остановился штатно", exc_info=True)
        if self.embedded is not None:
            assistant, api = self.embedded
            try:
                api.stop()
            except Exception:  # noqa: BLE001
                log.debug("API не остановился штатно", exc_info=True)
            try:
                assistant.shutdown()
            except Exception:  # noqa: BLE001
                log.debug("ядро не остановилось штатно", exc_info=True)
        try:
            self.root.destroy()
        except Exception:  # noqa: BLE001
            pass

    def run(self) -> int:
        try:
            self.root.mainloop()
        except KeyboardInterrupt:
            self._quit()
        return 0


# --------------------------------------------------------------------- запуск


def connect_or_start(*, debug: bool = False) -> tuple[Any, Any]:
    """Находит работающее ядро или поднимает своё (в этом же процессе).

    Возвращает (клиент, встроенное ядро или None). Если ядро подняли мы сами,
    окно обязано его остановить при выходе.
    """
    from .client import ApiClient

    client = ApiClient.discover()
    if client is not None:
        log.info("окно подключается к работающему ядру: %s", client.url)
        return client, None

    from ..core.assistant import create_assistant
    from .api import LocalApi

    assistant = create_assistant(debug=debug)
    api = LocalApi(assistant)
    info = api.start()
    log.info("окно подняло своё ядро: %s (порт %s)", api.url, info["port"])
    from .client import ApiTarget

    return ApiClient(ApiTarget(url=api.url, token=api.token)), (assistant, api)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="jarvis gui", description="Окно Jarvis (Tkinter)")
    parser.add_argument("--no-tray", action="store_true", help="без значка в трее")
    parser.add_argument("--start-minimized", action="store_true", help="сразу свернуть в трей")
    parser.add_argument("--debug", action="store_true", help="подробный лог")
    args = parser.parse_args(argv)

    ready, reason = tkinter_available()
    if not ready:
        print(reason, file=sys.stderr)
        print("Пока окно недоступно, ассистент работает в терминале: jarvis ask «текст»", file=sys.stderr)
        return 1

    from ..core.logging_setup import setup_logging

    setup_logging(debug=args.debug)
    log.info("масштаб Windows: %s", theme_module.enable_dpi_awareness())
    client, embedded = connect_or_start(debug=args.debug)
    try:
        app = GuiApp(client, tray=not args.no_tray, start_minimized=args.start_minimized,
                     embedded=embedded, debug=args.debug)
    except Exception:
        if embedded is not None:
            embedded[1].stop()
            embedded[0].shutdown()
        raise
    return app.run()
