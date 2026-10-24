#!/bin/bash
if command -v ufw >/dev/null 2>&1; then
  status=$(ufw status 2>/dev/null | head -1)
  echo "${status:-Не удалось узнать статус ufw.}"
elif command -v firewall-cmd >/dev/null 2>&1; then
  state=$(firewall-cmd --state 2>/dev/null)
  echo "Firewalld: ${state:-неизвестно}."
elif command -v nft >/dev/null 2>&1 && nft list ruleset 2>/dev/null | grep -q .; then
  echo "nftables активен, правила заданы."
else
  echo "Файрвол не настроен или не найден."
fi
