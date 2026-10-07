"""Маленькая замена Tkinter для тестов окна без экрана.

Настоящий Tkinter требует графическую среду, которой в тестовой песочнице нет.
Здесь собраны только те классы и методы, которыми пользуется окно Jarvis, чтобы
проверить связку «окно ↔ ядро»: обработчики кнопок, обновление списков, вкладки,
подтверждения и сохранение настроек.

Это не эмулятор Tk: рисуется ничего, зато ловятся опечатки и ошибки связки.
Внешний вид по-прежнему надо смотреть глазами на живой системе.
"""

from __future__ import annotations

import sys
import time
import types
from typing import Any, Callable

asked_questions: list[str] = []
answer_yesno: bool | None = False


class TclError(Exception):
    pass


class Widget:
    """Общее для всех виджетов: упаковка, привязки, настройка."""

    _counter = 0

    def __init__(self, master: Any = None, **options: Any):
        Widget._counter += 1
        self._id = Widget._counter
        self.master = master
        self.options = dict(options)
        self.state = str(options.get("state", "normal"))
        self.packed = False
        self.binds: dict[str, Callable] = {}
        self.children: list[Any] = []
        if isinstance(master, Widget):
            master.children.append(self)

    # ------------------------------------------------------------- геометрия
    def pack(self, **kwargs: Any) -> None:
        self.packed = True
        self.pack_options = kwargs

    def pack_propagate(self, *_args: Any) -> None:
        pass

    def pack_forget(self) -> None:
        self.packed = False

    def grid(self, **kwargs: Any) -> None:  # pragma: no cover - окно использует pack
        self.packed = True

    def winfo_height(self) -> int:
        return int(self.options.get("height", 0) or 0)

    def winfo_width(self) -> int:
        return int(self.options.get("width", 0) or 0)

    # -------------------------------------------------------------- настройка
    def configure(self, **kwargs: Any) -> None:
        self.options.update(kwargs)
        if "state" in kwargs:
            self.state = kwargs["state"]

    config = configure

    def cget(self, key: str) -> Any:
        return self.options.get(key)

    def bind(self, sequence: str, callback: Callable, add: str | None = None) -> None:
        self.binds[sequence] = callback

    def event_generate(self, sequence: str, **kwargs: Any) -> None:
        callback = self.binds.get(sequence)
        if callback is not None:
            callback(types.SimpleNamespace(**kwargs))

    def focus_set(self) -> None:
        pass

    def destroy(self) -> None:
        pass


class Frame(Widget):
    pass


class Label(Widget):
    def __init__(self, master: Any = None, **options: Any):
        super().__init__(master, **options)
        self.text = str(options.get("text", ""))

    def configure(self, **kwargs: Any) -> None:
        if "text" in kwargs:
            self.text = str(kwargs["text"])
        super().configure(**kwargs)

    config = configure


class Button(Widget):
    def __init__(self, master: Any = None, **options: Any):
        super().__init__(master, **options)
        self.command = options.get("command")
        self.clicks = 0

    def invoke(self) -> None:
        self.clicks += 1
        if self.command is not None:
            self.command()


class Checkbutton(Button):
    def __init__(self, master: Any = None, **options: Any):
        super().__init__(master, **options)
        self.variable = options.get("variable")

    def invoke(self) -> None:
        super().invoke()
        if self.variable is not None:
            self.variable.set(not self.variable.get())


class Entry(Widget):
    def __init__(self, master: Any = None, **options: Any):
        super().__init__(master, **options)
        self.value = str(options.get("text", ""))
        self.variable = options.get("textvariable")

    def insert(self, index: Any, text: str) -> None:
        self.value = text + self.value

    def delete(self, first: Any, last: Any = None) -> None:
        self.value = ""

    def get(self) -> str:
        return self.variable.get() if self.variable is not None else self.value

    def set(self, text: str) -> None:
        if self.variable is not None:
            self.variable.set(text)
        self.value = text


class Text(Widget):
    def __init__(self, master: Any = None, **options: Any):
        super().__init__(master, **options)
        self.lines: list[str] = []
        self.tags: dict[str, dict[str, Any]] = {}
        self.state = str(options.get("state", "normal"))

    def insert(self, index: Any, text: str, tags: Any = None) -> None:
        self.lines.append(text)
        if tags:
            name = tags[0] if isinstance(tags, tuple) else tags
            self.tags.setdefault(name, {})

    def delete(self, first: Any, last: Any = None) -> None:
        self.lines = []

    def get(self, first: Any = None, last: Any = None) -> str:
        return "".join(self.lines)

    def see(self, index: Any) -> None:
        pass

    def tag_configure(self, name: str, **options: Any) -> None:
        self.tags[name] = options

    def yview(self, *args: Any) -> None:
        pass

    def configure(self, **kwargs: Any) -> None:
        if "state" in kwargs:
            self.state = kwargs["state"]
        super().configure(**kwargs)

    config = configure


class Canvas(Widget):
    def __init__(self, master: Any = None, **options: Any):
        super().__init__(master, **options)
        self.items: dict[int, dict[str, Any]] = {}
        self._next = 0

    def _add(self, **kwargs: Any) -> int:
        self._next += 1
        self.items[self._next] = kwargs
        return self._next

    def create_oval(self, *args: Any, **kwargs: Any) -> int:
        return self._add(kind="oval", **kwargs)

    def create_window(self, *args: Any, **kwargs: Any) -> int:
        return self._add(kind="window", **kwargs)

    def itemconfigure(self, item: int, **kwargs: Any) -> None:
        self.items.setdefault(item, {}).update(kwargs)

    itemconfig = itemconfigure

    def bbox(self, *args: Any) -> tuple[int, int, int, int]:
        return (0, 0, 100, 100)

    def yview(self, *args: Any) -> None:
        pass

    def yview_scroll(self, *args: Any) -> None:
        pass

    def unbind_all(self, sequence: str) -> None:
        pass


class Variable:
    def __init__(self, master: Any = None, value: Any = None):
        self._value = value

    def get(self) -> Any:
        return self._value

    def set(self, value: Any) -> None:
        self._value = value


class BooleanVar(Variable):
    pass


class StringVar(Variable):
    pass


class IntVar(Variable):
    pass


class Tk(Widget):
    """Корневое окно с ручным циклом событий (``after`` + ``update``)."""

    def __init__(self, **options: Any):
        super().__init__(None, **options)
        self._jobs: list[tuple[float, Callable]] = []  # (когда запускать, что запускать)
        self.destroyed = False
        self.clipboard = ""
        self.withdrawn = False
        self.title_text = ""
        self.protocols: dict[str, Callable] = {}
        self.bindings: dict[str, Callable] = {}
        self.updated = 0

    # --------------------------------------------------------------- события
    def after(self, delay: int, callback: Callable) -> str:
        """Как в Tk: задача выполнится не раньше указанного времени."""
        self._jobs.append((time.monotonic() + max(0, int(delay)) / 1000.0, callback))
        return f"after#{len(self._jobs)}"

    def update(self) -> None:
        """Выполняет только те задачи, чьё время пришло."""
        self.updated += 1
        now = time.monotonic()
        due = [(moment, callback) for moment, callback in self._jobs if moment <= now]
        if due:
            ready = {id(callback) for _moment, callback in due}
            self._jobs = [(moment, callback) for moment, callback in self._jobs
                          if id(callback) not in ready]
            for _moment, callback in due:
                callback()

    def mainloop(self, n: int = 0) -> None:
        for _ in range(3):
            self.update()

    def protocol(self, name: str, callback: Callable) -> None:
        self.protocols[name] = callback

    def bind(self, sequence: str, callback: Callable, add: str | None = None) -> None:
        self.bindings[sequence] = callback

    def bind_all(self, sequence: str, callback: Callable) -> None:
        self.bindings[sequence] = callback

    def unbind_all(self, sequence: str) -> None:
        self.bindings.pop(sequence, None)

    # ------------------------------------------------------------ оформление
    def title(self, text: str) -> None:
        self.title_text = text

    def geometry(self, spec: str) -> None:
        self.geometry_spec = spec

    def minsize(self, width: int, height: int) -> None:
        self.min_size = (width, height)

    def withdraw(self) -> None:
        self.withdrawn = True

    def deiconify(self) -> None:
        self.withdrawn = False

    def lift(self) -> None:
        pass

    def focus_force(self) -> None:
        pass

    def resizable(self, *args: Any) -> None:
        pass

    def clipboard_clear(self) -> None:
        self.clipboard = ""

    def clipboard_append(self, text: str) -> None:
        self.clipboard += text

    def destroy(self) -> None:
        self.destroyed = True
        self._jobs.clear()

    def call(self, *args: Any) -> str:  # pragma: no cover
        return ""


def _make_ttk() -> types.ModuleType:
    module = types.ModuleType("tkinter.ttk")

    class Style:
        def __init__(self, master: Any = None):
            self.themes: list[str] = []
            self.settings: dict[str, dict[str, Any]] = {}

        def theme_use(self, name: str | None = None) -> str:
            if name:
                self.themes.append(name)
            return name or "clam"

        def configure(self, name: str, **options: Any) -> None:
            self.settings.setdefault(name, {}).update(options)

        def map(self, name: str, **options: Any) -> None:
            self.settings.setdefault(name, {}).update(options)

    class Scrollbar(Widget):
        def set(self, *args: Any) -> None:
            pass

    class Treeview(Widget):
        def __init__(self, master: Any = None, **options: Any):
            super().__init__(master, **options)
            self.rows: dict[str, dict[str, Any]] = {}
            self.headings: dict[str, str] = {}
            self.columns_cfg: dict[str, dict[str, Any]] = {}
            self.selected: tuple[str, ...] = ()

        def heading(self, column: str, **options: Any) -> None:
            self.headings[column] = str(options.get("text", ""))

        def column(self, column: str, **options: Any) -> None:
            self.columns_cfg.setdefault(column, {}).update(options)

        def insert(self, parent: str, index: Any, iid: str = "", values: Any = ()) -> str:
            key = iid or f"row{len(self.rows) + 1}"
            self.rows[key] = {"values": tuple(values), "parent": parent}
            return key

        def delete(self, *items: str) -> None:
            for item in items:
                self.rows.pop(item, None)

        def get_children(self, parent: str = "") -> tuple[str, ...]:
            return tuple(self.rows)

        def selection(self) -> tuple[str, ...]:
            return self.selected

        def selection_set(self, item: str) -> None:
            self.selected = (item,)

        def configure(self, **kwargs: Any) -> None:
            super().configure(**kwargs)

        config = configure

        def yview(self, *args: Any) -> None:
            pass

        def item(self, item: str, **options: Any) -> dict[str, Any]:
            row = self.rows.get(item, {})
            if options:
                row.update(options)
                self.rows[item] = row
            return row

    class Notebook(Widget):
        def __init__(self, master: Any = None, **options: Any):
            super().__init__(master, **options)
            self.tabs: list[Any] = []
            self.current = 0
            self.binds: dict[str, Callable] = {}

        def add(self, child: Any, **options: Any) -> None:
            self.tabs.append(child)

        def select(self, tab: Any = None) -> Any:
            if tab is not None:
                self.current = self.tabs.index(tab)
            return self.tabs[self.current]

        def index(self, tab: Any) -> int:
            return self.tabs.index(tab)

        def enable_traversal(self) -> None:
            pass

        def bind(self, sequence: str, callback: Callable, add: str | None = None) -> None:
            super().bind(sequence, callback)

        def event_generate(self, sequence: str, **kwargs: Any) -> None:
            callback = self.binds.get(sequence)
            if callback is not None:
                callback(types.SimpleNamespace(**kwargs))

    class Combobox(Entry):
        def __init__(self, master: Any = None, **options: Any):
            super().__init__(master, **options)
            self.values = list(options.get("values", []))

        def configure(self, **kwargs: Any) -> None:
            if "values" in kwargs:
                self.values = list(kwargs["values"])
            super().configure(**kwargs)

        config = configure

    module.Style = Style
    module.Frame = Frame
    module.Label = Label
    module.Button = Button
    module.Checkbutton = Checkbutton
    module.Entry = Entry
    module.Scrollbar = Scrollbar
    module.Treeview = Treeview
    module.Notebook = Notebook
    module.Combobox = Combobox
    return module


def _make_messagebox() -> types.ModuleType:
    module = types.ModuleType("tkinter.messagebox")

    def askyesno(title: str, message: str = "", **kwargs: Any) -> bool:
        asked_questions.append(message or title)
        return bool(answer_yesno)

    def showinfo(*args: Any, **kwargs: Any) -> None:
        pass

    def showerror(title: str, message: str = "", **kwargs: Any) -> None:
        asked_questions.append(message or title)

    module.askyesno = askyesno
    module.showinfo = showinfo
    module.showerror = showerror
    return module


class fake_tkinter:
    """Контекст: подменяет ``tkinter`` на время работы окна в тесте."""

    def __init__(self, *, yes: bool = False):
        global answer_yesno
        answer_yesno = yes
        asked_questions.clear()
        self.module = types.ModuleType("tkinter")
        self.module.ttk = _make_ttk()
        self.module.messagebox = _make_messagebox()
        self.module.Tk = Tk
        self.module.Frame = Frame
        self.module.Label = Label
        self.module.Button = Button
        self.module.Checkbutton = Checkbutton
        self.module.Entry = Entry
        self.module.Text = Text
        self.module.Canvas = Canvas
        self.module.Variable = Variable
        self.module.BooleanVar = BooleanVar
        self.module.StringVar = StringVar
        self.module.IntVar = IntVar
        self.module.TclError = TclError
        self.module.TOP = "top"
        self.module.BOTTOM = "bottom"
        self.module.LEFT = "left"
        self.module.RIGHT = "right"
        self.module.BOTH = "both"
        self.module.X = "x"
        self.module.Y = "y"
        self.module.END = "end"
        self.module.WORD = "word"
        self.module.NORMAL = "normal"
        self.module.DISABLED = "disabled"
        self.module.INSERT = "insert"
        self._saved: dict[str, Any] = {}

    def __enter__(self) -> types.ModuleType:
        for name, module in (("tkinter", self.module),
                             ("tkinter.ttk", self.module.ttk),
                             ("tkinter.messagebox", self.module.messagebox)):
            self._saved[name] = sys.modules.get(name)
            sys.modules[name] = module
        return self.module

    def __exit__(self, *exc: Any) -> None:
        for name, module in self._saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module
