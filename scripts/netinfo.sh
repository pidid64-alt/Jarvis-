#!/bin/bash
# Озвучивает локальный IP, точки заменены словом "точка" для внятного произношения.
ip=$(ip -4 addr show scope global 2>/dev/null | grep -oP '(?<=inet\s)\d+(\.\d+){3}' | head -1)

if [ -z "$ip" ]; then
  echo "Не вижу активного сетевого подключения."
else
  spoken=${ip//./ точка }
  echo "Локальный адрес: ${spoken}."
fi
