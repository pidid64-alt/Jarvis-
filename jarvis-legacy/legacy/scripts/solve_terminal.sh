#!/bin/bash
# solve_terminal.sh — «реши проблему»: снимает текст активного терминала и
# передаёт его задачей в Claude Code (вкладка claude из сессии кодинга).
#
# Безопасность: скрипт статичен, из голоса сюда не приходит ничего. Claude
# работает под СВОЕЙ системой разрешений, пользователь всё видит в терминале.
#
# DRYRUN=1 ./solve_terminal.sh — напечатать план действий, ничего не делая.
#
# Зависимости: xdotool, xclip (sudo pacman -S xclip), claude CLI.

set -uo pipefail

DRYRUN="${DRYRUN:-0}"
STEP_MAX_CHARS=2500   # сколько символов вывода терминала берём (хвост)

say_notify() {
    notify-send -a Jarvis "Реши проблему" "$1" 2>/dev/null || true
    echo "$1"
}

# --- 0. зависимости -------------------------------------------------------
for tool in xdotool xclip; do
    if ! command -v "$tool" >/dev/null 2>&1; then
        say_notify "Не установлен $tool. Поставь: sudo pacman -S $tool"
        exit 1
    fi
done

# --- 1. активное окно должно быть терминалом ------------------------------
WIN_ID=$(xdotool getactivewindow 2>/dev/null || true)
if [ -z "$WIN_ID" ]; then
    say_notify "Не вижу активного окна."
    exit 1
fi

CLASS=$(xdotool getwindowclassname "$WIN_ID" 2>/dev/null || echo "")
case "$CLASS" in
    *terminal*|*Terminal*|*konsole*) ;;          # окей, это терминал
    *)
        say_notify "Активное окно не терминал ($CLASS). Открой терминал с ошибкой."
        exit 1
        ;;
esac

# --- 2. снять текст буфера терминала --------------------------------------
OLD_CLIP=$(xclip -o -selection clipboard 2>/dev/null || true)

xdotool windowactivate --sync "$WIN_ID"
sleep 0.2
xdotool key --clearmodifiers ctrl+shift+a     # select all
sleep 0.3
xdotool key --clearmodifiers ctrl+shift+c     # copy
sleep 0.4

TEXT=$(xclip -o -selection clipboard 2>/dev/null | tail -c "$STEP_MAX_CHARS")

# восстановить буфер пользователя
if [ -n "$OLD_CLIP" ]; then
    printf '%s' "$OLD_CLIP" | xclip -selection clipboard 2>/dev/null || true
fi

if [ -z "${TEXT//[$' \t\r\n']/}" ]; then
    say_notify "Терминал пустой, нечего решать."
    exit 1
fi

# одна строка: Enter в claude TUI отправляет сообщение, новые строки сломают ввод
ONELINE=$(printf '%s' "$TEXT" | tr '\n' ' ' | sed 's/  */ /g')
TASK="Задача от Джарвиса: вот вывод моего терминала, разберись и исправь проблему под моим контролем: $ONELINE"

# --- 3. сессия кодинга: жива или запустить новую ---------------------------
SESSION_FILE="$HOME/.local/share/jarvis/coding_session.json"
CLAUDE_WIN=""
if [ -f "$SESSION_FILE" ] && command -v python3 >/dev/null 2>&1; then
    ACTIVE=$(python3 -c "
import json,sys
try:
    s=json.load(open('$SESSION_FILE'))
    print('yes' if s.get('active') and s.get('window_id') else 'no')
except Exception:
    print('no')")
    if [ "$ACTIVE" = "yes" ]; then
        CLAUDE_WIN=$(python3 -c "
import json
print(json.load(open('$SESSION_FILE')).get('window_id',''))")
        # окно ещё существует?
        if ! xdotool getwindowname "$CLAUDE_WIN" >/dev/null 2>&1; then
            CLAUDE_WIN=""
        fi
    fi
fi

if [ -z "$CLAUDE_WIN" ]; then
    if [ "$DRYRUN" = "1" ]; then
        echo "[dryrun] запустил бы start_coding_session.sh"
        echo "[dryrun] задача: ${TASK:0:120}..."
        exit 0
    fi
    bash "$HOME/jarvis/scripts/start_coding_session.sh" >/dev/null 2>&1 || true
    # перечитать id окна
    [ -f "$SESSION_FILE" ] && CLAUDE_WIN=$(python3 -c "
import json
try: print(json.load(open('$SESSION_FILE')).get('window_id',''))
except Exception: print('')" 2>/dev/null)
    sleep 3   # даём claude CLI прогрузиться
fi

if [ -z "$CLAUDE_WIN" ]; then
    say_notify "Не удалось открыть окно с Claude."
    exit 1
fi

# --- 4. передать задачу в вкладку claude -----------------------------------
if [ "$DRYRUN" = "1" ]; then
    echo "[dryrun] окно=$CLAUDE_WIN"
    echo "[dryrun] задача: ${TASK:0:160}..."
    exit 0
fi

xdotool windowactivate --sync "$CLAUDE_WIN"
sleep 0.2
xdotool key --clearmodifiers ctrl+Page_Down    # на вкладку claude
sleep 0.3
xdotool type --clearmodifiers --delay 8 "$TASK"
sleep 0.3
xdotool key --clearmodifiers Return

say_notify "Передал задачу Клоду, смотри в терминал."
