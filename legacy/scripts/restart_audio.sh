#!/bin/bash
# Перезапуск PulseAudio/PipeWire

systemctl --user restart pipewire pipewire-pulse wireplumber 2>/dev/null || pulseaudio -k && sleep 1 && pulseaudio --start
echo "Аудио система перезапущена"