#!/bin/bash
dns=$(resolvectl status 2>/dev/null | grep -m1 'DNS Server' | awk '{print $3}')
if [ -z "$dns" ]; then
  dns=$(grep -m1 '^nameserver' /etc/resolv.conf 2>/dev/null | awk '{print $2}')
fi
if [ -z "$dns" ]; then
  echo "Не нашёл настроенный ДНС."
else
  echo "ДНС-сервер: ${dns//./ точка }."
fi
