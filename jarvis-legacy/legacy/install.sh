#!/bin/bash
# Установка Jarvis Control Core на CachyOS/Arch + XFCE4.
# Идемпотентен: повторный запуск пропускает уже готовые шаги.
set -e

JARVIS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WHISPER_MODEL="base-q5_1"       # мультиязычная, квантованная, ~60МБ
PIPER_VOICE="ru_RU-dmitri-medium"

echo "== Jarvis Control Core: установка =="
echo "Каталог проекта: $JARVIS_DIR"

echo "-> Проверяю зависимости..."
MISSING=""
for bin in git cmake make python3 parecord paplay pactl notify-send xfconf-query; do
    command -v "$bin" >/dev/null 2>&1 || MISSING="$MISSING $bin"
done
if [ -n "$MISSING" ]; then
    echo "Не хватает бинарников:$MISSING"
    echo "На CachyOS/Arch поставь:"
    echo "  sudo pacman -S --needed git cmake base-devel python pipewire-pulse libnotify xfce4-settings"
    exit 1
fi

# ---------------------------------------------------------------------------
# 1. whisper.cpp
# ---------------------------------------------------------------------------
# Исходники не вендорятся. Пустой каталог часто остаётся после clone репозитория:
# whisper.cpp был записан как gitlink (mode 160000) без .gitmodules, git создаёт
# пустую папку, и проверка «каталог есть?» пропускала clone — cmake падал
# с «does not appear to contain CMakeLists.txt».
cd "$JARVIS_DIR"
if [ ! -f "whisper.cpp/CMakeLists.txt" ]; then
    echo "-> Клонирую whisper.cpp..."
    if [ -e "whisper.cpp" ]; then
        echo "   Каталог whisper.cpp есть, но исходников нет — переклонирую."
        rm -rf whisper.cpp
    fi
    git clone --depth 1 https://github.com/ggml-org/whisper.cpp.git "$JARVIS_DIR/whisper.cpp"
fi
if [ ! -f "whisper.cpp/CMakeLists.txt" ]; then
    echo "Ошибка: не удалось получить исходники whisper.cpp (нет CMakeLists.txt)."
    exit 1
fi
cd "$JARVIS_DIR/whisper.cpp"
if [ ! -f "build/bin/whisper-server" ]; then
    echo "-> Собираю whisper.cpp (пара минут на слабом CPU)..."
    cmake -B build -DCMAKE_BUILD_TYPE=Release -DWHISPER_BUILD_SERVER=ON
    cmake --build build -j "$(nproc)" --config Release
fi
if [ ! -f "build/bin/whisper-server" ]; then
    echo "Ошибка: не собрался build/bin/whisper-server. Смотри вывод cmake выше."
    exit 1
fi
cd "$JARVIS_DIR"

mkdir -p models
if [ ! -f "models/ggml-${WHISPER_MODEL}.bin" ]; then
    echo "-> Скачиваю модель Whisper (${WHISPER_MODEL}, мультиязычная)..."
    sh whisper.cpp/models/download-ggml-model.sh "$WHISPER_MODEL" "$JARVIS_DIR/models"
fi

# ---------------------------------------------------------------------------
# 2. Python venv + Piper
# ---------------------------------------------------------------------------
if [ ! -d "venv" ]; then
    echo "-> Создаю venv..."
    python3 -m venv venv
fi
echo "-> Ставлю piper-tts[http], requests и webrtcvad в venv..."
./venv/bin/pip install --quiet --upgrade pip
./venv/bin/pip install --quiet "piper-tts[http]" requests webrtcvad-wheels

if [ ! -f "models/${PIPER_VOICE}.onnx" ]; then
    echo "-> Скачиваю голос Piper (${PIPER_VOICE})..."
    ./venv/bin/python3 -m piper.download_voices "$PIPER_VOICE" --download-dir "$JARVIS_DIR/models"
fi

# ---------------------------------------------------------------------------
# 3. systemd --user юниты
# ---------------------------------------------------------------------------
echo "-> Ставлю systemd юниты..."
UNIT_DIR="$HOME/.config/systemd/user"
# mkdir -p молча «успевает», если путь — файл или битый симлинк, и следующий
# редирект падает с «Нет такого файла или каталога». Проверяем явно.
for p in "$HOME/.config" "$HOME/.config/systemd" "$UNIT_DIR"; do
    if [ -L "$p" ] && [ ! -d "$p" ]; then
        echo "Ошибка: $p — битый симлинк (указывает в никуда)."
        echo "Удали его: rm '$p'  — и запусти install.sh снова."
        exit 1
    fi
    if [ -e "$p" ] && [ ! -d "$p" ]; then
        echo "Ошибка: $p существует, но это не каталог."
        echo "Убери/переименуй его и запусти install.sh снова."
        exit 1
    fi
done
mkdir -p "$UNIT_DIR"
if [ ! -d "$UNIT_DIR" ] || [ ! -w "$UNIT_DIR" ]; then
    echo "Ошибка: нет доступа на запись в $UNIT_DIR."
    ls -ld "$HOME/.config" "$HOME/.config/systemd" "$UNIT_DIR" 2>&1 || true
    exit 1
fi
# Шаблоны в systemd/ рассчитаны на ~/jarvis; подставляем реальный путь проекта
# (клон часто лежит в ~/Jarvis- и т.п.). %h/.config/jarvis не трогаем.
for unit in "$JARVIS_DIR"/systemd/*.service; do
    dest="$UNIT_DIR/$(basename "$unit")"
    sed "s|%h/jarvis|$JARVIS_DIR|g" "$unit" > "$dest"
done
systemctl --user daemon-reload
systemctl --user enable --now jarvis-whisper.service jarvis-piper.service
echo "-> Жду, пока STT/TTS серверы поднимутся..."
sleep 3
systemctl --user enable --now jarvis.service jarvis-autonomy.service

# ---------------------------------------------------------------------------
# 4. Хоткей (XFCE)
# ---------------------------------------------------------------------------
echo "-> Вешаю хоткей Super+J на jarvis-trigger.sh..."
chmod +x "$JARVIS_DIR/jarvis-trigger.sh" "$JARVIS_DIR"/scripts/*.sh
if xfconf-query -c xfce4-keyboard-shortcuts \
    -p "/commands/custom/<Super>j" \
    -n -t string -s "$JARVIS_DIR/jarvis-trigger.sh" 2>/dev/null; then
    echo "   Готово: Super+J."
else
    echo "   Не получилось само — привяжи вручную:"
    echo "   Настройки -> Клавиатура -> Ярлыки приложений -> $JARVIS_DIR/jarvis-trigger.sh"
fi

# ---------------------------------------------------------------------------
# 5. Wake-word "Джарвис" (опционально, в дополнение к Super+J)
# ---------------------------------------------------------------------------
WAKEWORD_MODEL="$JARVIS_DIR/models/wakeword/jarvis.tflite"
if [ -f "$WAKEWORD_MODEL" ]; then
    echo "-> Нашёл модель wake-word, ставлю зависимости и юнит..."
    ./venv/bin/pip install --quiet openwakeword sounddevice numpy
    sed "s|%h/jarvis|$JARVIS_DIR|g" "$JARVIS_DIR/systemd/jarvis-wakeword.service" > "$UNIT_DIR/jarvis-wakeword.service"
    systemctl --user daemon-reload
    systemctl --user enable --now jarvis-wakeword.service
    echo "   Готово: слушает фоново на 'Джарвис', плюс Super+J как раньше."
else
    echo "-> Модели wake-word ($WAKEWORD_MODEL) нет — пропускаю этот шаг."
    echo "   Хоткей Super+J работает и без неё. Как добавить голосовой"
    echo "   триггер 'Джарвис' — см. README, раздел 'Wake-word'."
fi

cat <<EOF

===========================================================
Установка завершена. Осталось руками:
===========================================================

1) Часть команд требует sudo без пароля (перезапуск сети,
   обновление системы, выключение). Сначала проверь, какие
   из них реально спрашивают пароль:

     sudo systemctl restart NetworkManager
     sudo pacman -Syu --noconfirm
     sudo systemctl poweroff

   Для тех, что спрашивают, добавь строки в:

     sudo visudo -f /etc/sudoers.d/jarvis

   например:

     $(whoami) ALL=(root) NOPASSWD: /usr/bin/systemctl restart NetworkManager, /usr/bin/pacman -Syu --noconfirm, /usr/bin/systemctl poweroff

   Впиши только то, что реально нужно тебе — не всё подряд.

2) Проверь микрофон вручную:
     parecord --rate=16000 --channels=1 --format=s16le --file-format=wav /tmp/t.wav
     (Ctrl+C через пару секунд)
     paplay /tmp/t.wav

3) Проверь весь конвейер без хоткея:
     $JARVIS_DIR/venv/bin/python3 $JARVIS_DIR/jarvis.py --once --debug

4) Дальше — Super+J, подожди уведомление "Слушаю", говори команду.

Статус сервисов:
  systemctl --user status jarvis.service jarvis-whisper.service jarvis-piper.service
Логи:
  tail -f ~/.local/share/jarvis/jarvis.log
Список команд — commands.json, редактируется на лету (перечитывается
при каждом запросе, перезапуск демона не нужен).
EOF
