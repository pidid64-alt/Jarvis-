#!/bin/bash
if ! command -v zramctl >/dev/null 2>&1; then
  echo "zramctl не установлен."
  exit 0
fi
line=$(zramctl --noheadings --output DISKSIZE,DATA,COMPR 2>/dev/null | head -1)
if [ -z "$line" ]; then
  echo "Zram не активен."
else
  read -r disksize data compr <<< "$line"
  echo "Zram: размер ${disksize}, занято ${data}, сжато до ${compr}."
fi
