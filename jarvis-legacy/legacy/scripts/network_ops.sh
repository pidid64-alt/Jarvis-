#!/bin/bash
# Network operations
operation="${1}"

case "$operation" in
  active_connections)
    ss -tunap 2>/dev/null | grep ESTAB | wc -l
    ;;
  bandwidth_usage)
    # Shows current bandwidth usage
    if command -v vnstat &> /dev/null; then
      vnstat --oneline | cut -d';' -f5
    else
      echo "vnstat не установлен"
    fi
    ;;
  connected_wifi)
    nmcli -t -f active,ssid dev wifi | grep '^yes' | cut -d: -f2
    ;;
  wifi_signal)
    nmcli -f IN-USE,SIGNAL dev wifi | grep '*' | awk '{print $2}'
    ;;
  local_ips)
    ip -4 addr show | grep inet | grep -v 127.0.0.1 | awk '{print $2}' | cut -d/ -f1 | tr '\n' ', ' | sed 's/, $//'
    ;;
  ping_latency)
    ping -c 3 -W 2 8.8.8.8 2>/dev/null | tail -1 | awk -F '/' '{print $5}' | xargs -I {} echo "{} мс"
    ;;
esac
