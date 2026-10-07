# Разработчикам Jarvis

Документ для тех, кто будет менять код: как устроен проект, где что лежит и как
правильно править внешний вид окна, не разваливая остальное. Для обычного
использования есть README.

```
jarvis/
  core/        логика: маршрутизация, права, журнал, миграция, настройки
  skills/      навыки-плагины: одна папка = один навык (skill.json + handler.py)
  providers/   модель, распознавание, синтез, поиск, звук, слово-активатор
  platform/    различия Linux и Windows
  interfaces/  api.py, client.py, cli.py, daemon.py, gui.py, tray.py, theme.py
  config/      config.default.toml и i18n/ru.json
tests/         тесты: ядро, навыки, API, окно (через подставной Tkinter), тема
tools/         инструменты разработки: макеты, проверка контраста, замеры памяти
legacy/        прежняя версия — источник данных для `jarvis migrate`
docs/          отчёты по этапам и картинки
```

Правила, которые соблюдаются в коде:

* **в окне нет логики ассистента** — окно только показывает и спрашивает, все
  решения принимает ядро;
* **в окне нет цветов, шрифтов и отступов числами** — они в `theme.py`;
* **секреты только в `.env`**, в настройках — ссылки `${ИМЯ}`;
* **тяжёлые части подключаются лениво** (распознавание, синтез, слово-активатор);
* **тесты не требуют настоящего экрана** — окно проверяется через подставной
  Tkinter (`tests/fake_tk.py`).

---

## Тема окна

Весь внешний вид собран в одном файле — `jarvis/interfaces/theme.py`. Окно
(`gui.py`) берёт оттуда палитру, шкалы и шрифты и **не содержит ни одного цвета
и ни одного отступа числом**. Это проверяется тестом
`tests/test_theme.py::OneSourceOfTruthTests`, так что нарушить правило случайно
не получится.

### Что внутри темы

| Что | Где | Значение |
| --- | --- | --- |
| Отступы | `SPACE` | 4 / 8 / 12 / 16 / 24 (`xs`, `s`, `m`, `l`, `xl`) |
| Скругления | `RADIUS` | `button` 6, `field` 6, `card` 8, `bubble` 10, `pill` — «таблетка» |
| Шрифты | `FONTS` | `title` 21, `heading` 17, `body` 15, `caption` 13, `mono` 14 |
| Семейства | `FAMILIES` | Windows: Segoe UI Variable / Segoe UI; Linux: Noto Sans, DejaVu Sans; моно — Cascadia Mono, Consolas, DejaVu Sans Mono |
| Палитры | `DARK`, `LIGHT` | роли: `bg`, `surface`, `surface2`, `input_bg`, `border`, `field_border`, `text`, `muted`, `accent`, `on_accent`, `user_bg`, `user_text`, `assistant_bg`, `assistant_text`, `ok`, `warn`, `err`, `chip_bg`, `chip_text` |
| Состояния | `STATE_COLORS` | `idle`, `listening`, `thinking`, `speaking`, `waiting_confirmation`, `error` — свои цвета для каждой темы |
| Пороги | `TEXT_CONTRAST_MIN` 4.5, `GRAPHIC_CONTRAST_MIN` 3.0 | проверяются тестом |

Плюс функции: `system_theme()` (тема системы: реестр Windows, `defaults` на
macOS, `gsettings`/`GTK_THEME` на Linux), `resolve()` (превращает `system` в
`dark` или `light`), `palette()`, `state_color()`, `font_spec()`,
`resolve_families()`, `apply_ttk()`, `enable_dpi_awareness()`, `tk_scaling()`,
`contrast()`, `audit()`.

### Как поменять цвет

1. Откройте `jarvis/interfaces/theme.py`, найдите роль в `DARK` или `LIGHT`
   (например, `"accent"`).
2. Поменяйте значение. Если это текст, проверьте контраст:
   `python tools/contrast_check.py --all`.
3. Прогоните тесты: `python -m pytest tests/test_theme.py -q` — они не дадут
   оставить пару «текст на фоне» ниже 4.5:1, а состояние — ниже 3:1.
4. Обновите картинки для документации:
   `python tools/style_preview.py --style tokens --theme both` (лист токенов) и
   `python tools/gui_preview.py --theme dark --out docs/gui-preview-dark.png`.

Важно: `field_border` (границы полей ввода) проверяется строго — 3:1, потому
что по границе ищут поле глазами; `border` (рамки карточек, разделители)
обязан лишь различаться (`BORDER_CONTRAST_MIN = 1.2`), иначе интерфейс станет
шумным.

### Как поменять шрифт или размер

* Размеры — в `FONTS` (например, основной текст `body` 15). Правьте там, а не
  в окне: окно везде использует `self.font("body")`.
* Семейства — в `FAMILIES`, списком предпочтений. Первое доступное в системе и
  побеждает; сравнение без учёта регистра. Если ничего не подошло, берётся
  `TkDefaultFont`/`TkFixedFont` — окно останется читаемым на любой системе.
* Ничего не скачивается: используются системные шрифты либо свободные с
  разрешающей лицензией (см. раздел «Лицензии»).

### Как добавить состояние элемента

Состояния кнопок, полей и вкладок задаются в `ttk_style_config()` и
`ttk_style_map()` (там же — `Accent.TButton` для главной кнопки, `Card.TFrame`
для карточек). Добавили состояние — проверьте его тестом рядом с
`tests/test_theme.py::TtkStyleTests`.

### Светлая, тёмная и системная тема

В настройках (`assistant.theme`) хранится `system`, `dark` или `light`.
`system` — значение по умолчанию: окно спрашивает тему у системы и падает в
тёмную, если узнать не удалось. Смена темы применяется на лету:
`GuiApp.set_theme()` перекрашивает и ttk-стили, и обычные виджеты (они
записаны в `self._themed`), и метки чата.

### Масштаб 125 % и 150 % в Windows

Перед созданием окна вызывается `enable_dpi_awareness()` (иначе система
растягивает картинку и текст становится мыльным), а `tk_scaling(root)`
подгоняет масштаб Tk под фактический DPI. На Linux и macOS это не требуется.

### Контраст и проверка глазами

* `python tools/contrast_check.py` — таблица пар «текст на фоне», состояния и
  границы; выходной код 1, если что-то ниже порога.
* `python tools/style_preview.py --style tokens --theme both` — лист токенов
  (`docs/style/tokens-*.png`): палитра, шкалы, шрифты, состояния кнопок;
  рисуется из темы, поэтому не может разойтись с кодом.
* Макеты окна: `python tools/style_preview.py --style fluent --page chat --theme dark`.
* Замеры памяти: `python tools/measure_memory.py` (работает и на Windows).

---

## Лицензии

Код Jarvis — собственный; сторонние проекты подключаются отдельно и только с
разрешающими лицензиями:

| Что | Лицензия | Где используется |
| --- | --- | --- |
| Python / Tkinter (tcl/tk) | PSF / BSD-style | язык и окно |
| Piper (`rhasspy/piper`) | MIT | синтез речи |
| whisper.cpp (`ggerganov/whisper.cpp`) | MIT | распознавание речи |
| openWakeWord (`dscripka/openWakeWord`) | Apache-2.0 | слово-активатор |
| pystray | LGPL-3.0 | значок в трее (необязательно) |
| Pillow | HPND | только инструменты документации, приложению не нужен |
| Segoe UI Variable, Segoe UI, Consolas, Cascadia Mono | Microsoft, входят в Windows | шрифты интерфейса в Windows |
| Noto Sans, Noto Sans Mono | SIL OFL 1.1 | шрифты интерфейса в Linux |
| DejaVu Sans, DejaVu Sans Mono | Bitstream Vera / свободная | запасные шрифты в Linux |

Иконки рисуются кодом (Canvas) — сторонних наборов иконок в проекте нет, поэтому
и вопроса об их лицензии не возникает.

---

## Проверки перед коммитом

```bash
python -m pytest -q                  # весь набор тестов
python tools/contrast_check.py       # контраст палитр
python tools/measure_memory.py       # память (по желанию)
python -m compileall -q jarvis tools tests
```
