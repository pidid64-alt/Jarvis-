#!/bin/bash
# Погода через wttr.in (используй переменную JARVIS_WEATHER_LOCATION для города)
LOCATION="${JARVIS_WEATHER_LOCATION:-Moscow}"

weather=$(curl -sf --max-time 8 "wttr.in/${LOCATION}?format=%C+%t" 2>/dev/null)
if [ -z "$weather" ]; then
    echo "Не удалось получить прогноз погоды."
    exit 0
fi

echo "Погода в ${LOCATION}: ${weather}."
