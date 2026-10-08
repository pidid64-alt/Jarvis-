#!/bin/bash
if ! command -v docker >/dev/null 2>&1; then
  echo "Докер не установлен."
  exit 0
fi
running=$(docker ps -q 2>/dev/null | wc -l)
echo "Запущено контейнеров: ${running}."
