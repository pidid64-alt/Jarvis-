#!/bin/bash
# Clipboard operations
operation="${1}"

case "$operation" in
  save_to_file)
    timestamp=$(date +%Y%m%d_%H%M%S)
    xclip -selection clipboard -o > "$HOME/clipboard_$timestamp.txt" 2>/dev/null
    echo "Сохранено в clipboard_$timestamp.txt"
    ;;
  append_time)
    current=$(xclip -selection clipboard -o 2>/dev/null)
    timestamp=$(date '+%Y-%m-%d %H:%M:%S')
    echo -n "$current [$timestamp]" | xclip -selection clipboard
    echo "Время добавлено"
    ;;
  count_chars)
    xclip -selection clipboard -o 2>/dev/null | wc -c
    ;;
  count_words)
    xclip -selection clipboard -o 2>/dev/null | wc -w
    ;;
  to_uppercase)
    xclip -selection clipboard -o 2>/dev/null | tr '[:lower:]' '[:upper:]' | xclip -selection clipboard
    echo "Преобразовано в верхний регистр"
    ;;
  to_lowercase)
    xclip -selection clipboard -o 2>/dev/null | tr '[:upper:]' '[:lower:]' | xclip -selection clipboard
    echo "Преобразовано в нижний регистр"
    ;;
esac
