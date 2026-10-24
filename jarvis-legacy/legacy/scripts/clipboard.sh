#!/bin/bash
if ! command -v xclip >/dev/null 2>&1; then
  echo "xclip не установлен."
  exit 0
fi
text=$(xclip -selection clipboard -o 2>/dev/null)
if [ -z "$text" ]; then
  echo "Буфер обмена пуст."
else
  echo "${text:0:200}"
fi
