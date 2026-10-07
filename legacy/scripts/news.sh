#!/bin/bash
# Топ-5 заголовков из RSS. Источник можно переопределить:
#   JARVIS_NEWS_FEED_URL — URL фида (по умолчанию Лента.ру)
#   JARVIS_NEWS_COUNT    — сколько заголовков озвучить (по умолчанию 5)
FEED="${JARVIS_NEWS_FEED_URL:-https://lenta.ru/rss/last24}"
COUNT="${JARVIS_NEWS_COUNT:-5}"

xml=$(curl -sf --max-time 10 "$FEED")
if [ -z "$xml" ]; then
  echo "Не смог получить новости — сеть или источник недоступны."
  exit 0
fi

titles=$(printf '%s' "$xml" | python3 -c "
import sys, xml.etree.ElementTree as ET
try:
    root = ET.fromstring(sys.stdin.read())
except ET.ParseError:
    sys.exit(0)
# RSS 2.0 (channel/item/title) и Atom (entry/title)
items = root.findall('.//item/title')
if not items:
    ns = {'a': 'http://www.w3.org/2005/Atom'}
    items = root.findall('.//a:entry/a:title', ns)
for t in items[:${COUNT}]:
    text = (t.text or '').strip()
    if text:
        print(text.rstrip('.') + '.')
")

if [ -z "$titles" ]; then
  echo "Источник новостей ответил, но заголовков не нашлось."
  exit 0
fi
echo "$titles"
