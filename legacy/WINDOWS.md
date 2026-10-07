# Jarvis на Windows 10/11

Полноценный голосовой контур на Windows: **Win+J → запись → whisper.cpp →
white-list команд → Piper → голосовой ответ**. Принцип безопасности тот же:
распознанный голос не попадает в shell — он только выбирает команду из
`commands-win.json`.

```
Win+J (hotkey-win.py) ──┐
                        ▼
"Джарвис" (wakeword.py) ──► trigger-файл ──► jarvis.py (демон)
                                                 │
                              whisper-server :8081 (STT)
                                                 │
                              commands-win.json (white-list, фразы те же)
                                                 │
                              cmd / PowerShell / python-хелперы scripts_win\
                                                 │
                              piper http_server :5000 (TTS) → winsound
```

## Отличия от Linux

| Что | Linux | Windows |
|---|---|---|
| Хоткей Super+J | XFCE xfconf | **Win+J**, `hotkey-win.py` (Win32 `RegisterHotKey`, без админки) |
| Пробуждение демона | SIGUSR1 | **trigger-файл** `~/.local/share/jarvis/trigger` (SIGUSR1 остаётся запасным на Linux) |
| Запись микрофона | parecord (PulseAudio) | **sounddevice** (WASAPI/PortAudio), VAD общий |
| Команды | `commands.json` (324) | `commands-win.json` (270) — фразы/описания совпадают, тела команд переписаны |
| Скрипты статусов | `scripts/*.sh` | `scripts_win/*.ps1` (UTF-8-вывод) |
| Уведомления | notify-send | Toast через PowerShell (создаётся в state-каталоге) |
| Воспроизведение | paplay | `winsound` |
| Блокировки | flock | flock / `msvcrt.locking` (`platform_support.file_lock`) |
| Wake-word | openWakeWord, tflite | openWakeWord, **onnx** (tflite-runtime Linux-only); модель `models/wakeword/jarvis.onnx` уже в репо |
| systemd | 5 юнитов | Scheduled Tasks (регистрирует `install-win.ps1`) |
| LLM-ключ | systemd EnvironmentFile | тот же файл `%USERPROFILE%\.config\jarvis\env` (`KEY=VALUE`), читает сам Jarvis |
| Режим кодинга (dictation в терминал) | xdotool | **не поддерживается** (честно отключён, не «иногда работает») |

## Установка

Требования: Windows 10/11 x64, **Python 3.11–3.13** (3.14 пока не подходит —
нет `webrtcvad-wheels`), доступ в интернет (первый запуск качает
whisper-server ~9МБ; модели Whisper/Piper и wake-word уже в `models/`).

```powershell
cd path\to\Jarvis-
powershell -ExecutionPolicy Bypass -File install-win.ps1
```

Идемпотентен, как и `install.sh`:

1. создаёт `venv`, ставит `requests`, `webrtcvad-wheels`, `sounddevice`,
   `numpy`, `piper-tts[http]` (+ `openwakeword`, если есть модель);
2. качает готовый `whisper-server.exe` (релиз
   [whisper.cpp v1.9.2](https://github.com/ggml-org/whisper.cpp/releases/tag/v1.9.2),
   MSVC не нужен) в `whisper-win\Release\`;
3. при отсутствии докачивает модели в `models\`;
4. регистрирует Scheduled Tasks на вход в систему: `jarvis-whisper`,
   `jarvis-piper`, `jarvis`, `jarvis-autonomy`, `jarvis-hotkey`
   (+ `jarvis-wakeword`, если положил модель);
5. сразу запускает их и создаёт шаблон env-файла.

Откат задач: `install-win.ps1 -Uninstall`.

## Проверка по шагам

```powershell
# 1. Серверы поднялись?
curl http://127.0.0.1:8081/health
curl -X POST -H "Content-Type: application/json" -d "{\"text\":\"Привет\"}" http://127.0.0.1:5000/synthesize -o reply.wav

# 2. Весь конвейер разом, без хоткея (сначала спросит микрофон)
venv\Scripts\python.exe jarvis.py --once --debug

# 3. Хоткей: нажми Win+J, дождись «Слушаю…», скажи «который час»
```

Логи: `%USERPROFILE%\.local\share\jarvis\jarvis.log`.
Статус задач: `Get-ScheduledTask jarvis* | Format-Table TaskName, State`.
Список команд: `commands-win.json` (редактируется на лету, перечитывается
на каждый запрос).

## Что работает из команд

270 из 324: статусы (диск/память/батарея/сеть/процессы), питание
(выключение/перезагрузка/блокировка/сон/гибернация — с голосовым
подтверждением), громкость и яркость (медиа- и system-клавиши, WMI),
окна (прижать/центрировать/поверх всех — user32), вкладки и зум браузера
(AppActivate + SendKeys), Discord (звонки через Ctrl+K, горячие клавиши
те же), приложения и URL, скриншоты, корзина, winget-обновления,
таймеры/помодоро, буфер обмена (Get/Set-Clipboard), пинг/IP/DNS,
журнал событий, задачи расписания, wake-word по «Джарвис».

Автономные проверки (`autonomy.py`) на Windows: диск, память, батарея
(ctypes), обновления (winget), упавшие сервисы (Get-Service), ошибки
загрузки (Get-WinEvent), интернет (ping). Температура CPU честно
отвечает «датчик недоступен» — стандартный API Windows её не отдаёт,
ложных тревог не будет.

## Чего на Windows нет (55 команд)

Личные linux-шаблоны (`restart_litvin/noxy`, `rat_hunt`, `ollama restart`),
системные細 Arch (pacman/AUR/zram/earlyoom/governor через cpupower),
x11-утилиты без аналогов (рабочие столы, xtrlock, redshift, clipman),
режим кодинга/`solve_terminal`, запись микрофона как отдельная команда.
Они **не включены** в `commands-win.json`, а не замолчаны: LLM о них не
знает и не пытается вызвать. Добавить свою — объект в `commands-win.json`,
тело команды на cmd/PowerShell/Python, плейсхолдер `{base}` подставит
каталог проекта.

## Отладка

```powershell
# Логи
Get-Content $env:USERPROFILE\.local\share\jarvis\jarvis.log -Tail 50 -Wait

# Разовый прогон
venv\Scripts\python.exe jarvis.py --once --debug

# Wake-word (печатает score, Ctrl+C для выхода)
venv\Scripts\python.exe wakeword.py --debug

# Хоткей в консоли (видно нажатия Win+J)
venv\Scripts\python.exe hotkey-win.py
```

Если STT/TTS молчат — сначала `Get-ScheduledTask jarvis*`, потом логи
задач: `Get-ScheduledTaskInfo jarvis-whisper` и журнал «Система».
