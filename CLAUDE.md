# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Jarvis Control Core** — Local voice control for CachyOS/Arch + XFCE: hotkey → record → whisper.cpp STT → whitelist command match → execute → Piper TTS → voice response. Fully local, no cloud.

Key security principle: **Recognized voice text NEVER becomes part of shell commands**. It only SELECTS which pre-defined command from `commands.json` runs. Commands are static, written by you in advance.

## Architecture

```
Super+J ──────────┐
                   ▼
"Джарвис" → wakeword.py (SIGUSR1) → jarvis.py (daemon) → whisper-server (STT, :8081)
                                          │                        │
                                          │                 recognized text
                                          ▼                        │
                                   commands.json  ◄────────────────┘
                                   (whitelist, fuzzy-match)
                                          │
                                          ▼
                                   execute shell command
                                          │
                                          ▼
                                   piper http_server (TTS, :5000)  →  paplay
```

Two independent wake methods: hotkey (Super+J) and wake-word ("Джарвис"). Both send `SIGUSR1` to jarvis.py PID.

## Core Files

| File | Purpose |
|------|---------|
| `jarvis.py` | Main daemon orchestrator: signal → record → STT → match → exec → TTS |
| `config.json` | Ports, language, recording duration, confidence thresholds, VAD settings |
| `commands.json` | Whitelist of voice commands — hot-reloaded on every request |
| `scripts/*.sh` | Helpers for commands with "speaking" output (status, disk, IP, etc.) |
| `jarvis-trigger.sh` | Wakes daemon via hotkey (sends SIGUSR1) |
| `wakeword.py` | Optional: background listener for "Джарвис" wake-word via openWakeWord |
| `wakeword_config.json` | Wake-word threshold, cooldown, model path |
| `systemd/*.service` | 4 units: whisper-server, piper-server, jarvis, wakeword (optional) |
| `install.sh` | Idempotent installer: builds whisper.cpp, installs Piper, systemd, hotkey, wake-word |

## Common Development Commands

### Build/Install
```bash
# Full installation (idempotent)
./install.sh

# Manual steps if needed:
cd whisper.cpp && cmake -B build -DCMAKE_BUILD_TYPE=Release && cmake --build build -j$(nproc)
./venv/bin/pip install --quiet "piper-tts[http]" requests webrtcvad-wheels
```

### Run/Debug
```bash
# One-shot pipeline test (no daemon)
venv/bin/python3 jarvis.py --once --debug

# Run daemon manually
venv/bin/python3 jarvis.py --debug

# Test wake-word listener
venv/bin/python3 wakeword.py --debug

# Test STT server
curl http://127.0.0.1:8081/health

# Test TTS server
curl -X POST -H 'Content-Type: application/json' -d '{"text":"Тест"}' http://127.0.0.1:5000/synthesize -o /tmp/test.wav && paplay /tmp/test.wav
```

### Service Management
```bash
# Status
systemctl --user status jarvis.service jarvis-whisper.service jarvis-piper.service jarvis-wakeword.service

# Logs
tail -f ~/.local/share/jarvis/jarvis.log
tail -f ~/.local/share/jarvis/wakeword.log

# Restart
systemctl --user restart jarvis.service
```

### Microphone Test
```bash
parecord --rate=16000 --channels=1 --format=s16le --file-format=wav /tmp/test.wav
# Ctrl+C after a few seconds
paplay /tmp/test.wav
```

## Adding Voice Commands

Edit `commands.json` — add object to `commands` array:

```json
{
  "id": "my_command",
  "phrases": ["фраза один", "вариант фразы два"],
  "command": "your-shell-command",
  "response": "What to say in reply",
  "speak_output": false,
  "speak_before": false,
  "background": false
}
```

Key fields:
- **speak_output: true** — speaks stdout of command (for status queries)
- **speak_before: true** — speaks response BEFORE command runs (required for suspend/lock/poweroff/reboot/logout — otherwise TTS won't finish)
- **background: true** — runs async, speaks response immediately, done_message on completion
- **min_score** — per-command confidence threshold (default 0.6). Critical commands (poweroff, reboot, logout, system_update) use 0.85-0.9
- **confirm: true** — requires voice confirmation ("Точно выключить? Скажи да") before executing

File is hot-reloaded on every request — no daemon restart needed.

## Matcher Logic and Request Pipeline (jarvis.py)

Поток обработки реплики после STT — три ступени, по возрастанию задержки:

1. **Exact fast-path** (`match_commands_exact`): все непересекающиеся ТОЧНЫЕ
   вхождения фраз в текст (жадно от самой длинной, дедуп по id, порядок как в
   речи). Нашлись → команды исполняются локально мгновенно, БЕЗ LLM. Защита
   от «проглатывания»: если слов вне найденных фраз больше
   `exact_max_extra_words` (default 6) — реплика уходит на ступень 2.
   Здесь же работают мультикоманды без сети («открой дискорд и какая погода»).
2. **LLM-маршрут** (`try_llm_route`) — свободная речь, опечатки, мультикоманды.
3. **Fuzzy fallback** (`match_command`, одиночный результат):
   difflib.SequenceMatcher только если сказано не меньше слов, чем во фразе;
   среди точных вхождений выбирается самое длинное/специфичное;
   per-command `min_score` переопределяет глобальный `match_threshold`.

## LLM Route (свободная речь + мультикоманды, 2026-08)

Ступень 2: текст идёт в LLM-парсер (`try_llm_route` → `parse_intent`,
OpenRouter `https://openrouter.ai/api/v1`, модель `nvidia/nemotron-3-super-120b-a12b:free`).
LLM возвращает строгий JSON со СПИСКОМ действий `{"actions": [{command|speak|ask}, ...]}`
(до 4; старый одиночный формат тоже принимается):

- несколько просьб в одной реплике → элементы массива в порядке произнесения,
  исполняются последовательно, каждое озвучивает свой ответ;
- видит ТОЛЬКО id/tags/descriptions команд — поле `command` (shell) в промпт не попадает;
- каждый элемент проверяется по whitelist (`_validate_action`), dangerous-командам
  форсируется подтверждение; несуществующий id посреди батча пропускается (не fallback —
  часть действий уже исполнена);
- любая ошибка ДО исполнения (нет сети/ключа, битый JSON, пустой список) → молча
  fallback на `match_command`;
- контекст диалога 30с: `conversation_state.py` (STATE_DIR/conversation_state.json).

Ключ: env `OPENROUTER_API_KEY` из `~/.config/jarvis/env`
(`EnvironmentFile=-` в юнитах; отсутствие файла = работа без LLM).
Контрольный прогон: `scripts/test_llm.sh` (5 фраз + замер задержки).
Автономка `autonomy.py` использует тот же конфиг: LLM решает skip/notify,
результат пишется в STATE_DIR/inbox.json и озвучивается демоном раз в ~120с.

## VAD Recording (Voice Activity Detection)

Uses `webrtcvad` when available (installed via `webrtcvad-wheels`):
- Plays "beep" (config: `beep_command`) after trigger
- Waits for speech start (`vad_wait_speech_seconds`, default 4s)
- Records until `vad_silence_seconds` (default 0.9s) of silence after speech
- Hard cap: `max_record_seconds` (default 12s)
- Falls back to fixed `record_seconds` (4s) if webrtcvad not installed

## Wake-Word Setup (Optional)

1. Train model via [openWakeWord Colab](https://github.com/dscripka/openWakeWord) (`notebooks/automatic_model_training.ipynb`) with target word "Джарвис"
2. Place `.tflite` model at `models/wakeword/jarvis.tflite`
3. Re-run `./install.sh` — detects model, installs deps, enables `jarvis-wakeword.service`

## Sudoers Template

`install.sh` prints template for `/etc/sudoers.d/jarvis` — only add commands that actually prompt for password during manual testing:

```bash
# Example (adapt to your needs):
youruser ALL=(root) NOPASSWD: /usr/bin/systemctl restart NetworkManager, /usr/bin/pacman -Syu --noconfirm, /usr/bin/systemctl poweroff
```

## Scripts in `scripts/`

Each script outputs concise text suitable for TTS. Common patterns:
- `status.sh` — full system summary (CPU, RAM, disk, uptime)
- `diskspace.sh` — free space
- `netinfo.sh` — local/public IP
- `cputemp.sh`, `battery.sh`, `topprocs.sh`, `meminfo.sh`, etc.
- `checkupdates.sh`, `aurupdates.sh` — package updates (with timeout)
- `dockerstatus.sh`, `gitdirty.sh`, `failedservices.sh`, `recentlogs.sh`
- `window_control.sh`, `browser_tab.sh`, `browser_zoom.sh` — X11 window/browser automation via xdotool/wmctrl
- `discord_call.sh`, `discord_video_call.sh`, `discord_hangup.sh` — Discord automation
- `clipboard_ops.sh`, `file_ops.sh`, `network_ops.sh`, `system_info.sh` — grouped utilities

## Key Implementation Details

- **State dir**: `~/.local/share/jarvis/` (pid, logs, command.wav, reply.wav)
- **Config loading**: `config.json` merges over `DEFAULT_CONFIG` in jarvis.py
- **Commands loading**: Re-read from `commands.json` on every voice request
- **Signal handling**: SIGUSR1 triggers recording; SIGTERM cleanly exits daemon
- **Threading**: `processing_lock` prevents concurrent command handling; background commands use daemon threads with watchers
- **Logging**: File always, stdout with `--debug` or `--once`
- **Notifications**: `notify-send` for desktop notifications (title "Jarvis")

## Resources on i3-10110U / 3.6GB RAM

- whisper-server (base-q5_1): ~150-250MB resident
- piper-server (medium voice): ~150-250MB resident
- wakeword.py (openWakeWord): ~50-80MB resident
- LLM-парсер внешний (OpenRouter, cloud): локальную память не занимает;
  при недоступности сети/ключа голосовые команды работают через whitelist-matcher

If memory tight: switch whisper to `tiny-q5_1` (~30MB) or run servers on-demand instead of resident.