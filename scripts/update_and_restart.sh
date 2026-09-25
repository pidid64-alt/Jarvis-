#!/bin/bash
# update_and_restart.sh — ищет обновы и перезапускает всё (Linux)
# Сэр-стайл: весело, коротко, рандомные интро как в autonomy
# Использование: bash scripts/update_and_restart.sh [--auto-reboot] [--skip-update] [--only-services]

set -u
intros=("Сэр, я тут подглядел —" "Сэр, мне тут птичка нашептала, что" "Сэр, докладываю —")
intro() { local r=$((RANDOM % ${#intros[@]})); echo "${intros[$r]} $*"; }

AUTO_REBOOT=0
SKIP_UPDATE=0
ONLY_SERVICES=0
for a in "$@"; do
  case "$a" in --auto-reboot) AUTO_REBOOT=1;; --skip-update) SKIP_UPDATE=1;; --only-services) ONLY_SERVICES=1;; esac
done

upd_total=0
upd_installed=0
reboot_needed=0

# 1) Поиск обнов
if [ "$SKIP_UPDATE" -eq 0 ] && [ "$ONLY_SERVICES" -eq 0 ]; then
  echo "$(intro "ищу обновления...")"
  if command -v checkupdates >/dev/null 2>&1; then
    upd_total=$(checkupdates 2>/dev/null | wc -l); upd_total=$(echo "$upd_total" | tr -d ' ')
  elif command -v pacman >/dev/null 2>&1; then
    upd_total=$(pacman -Qu 2>/dev/null | wc -l); upd_total=$(echo "$upd_total" | tr -d ' ')
  elif command -v apt >/dev/null 2>&1; then
    upd_total=$(apt list --upgradable 2>/dev/null | grep -c upgradable || echo 0)
  elif command -v dnf >/dev/null 2>&1; then
    upd_total=$(dnf check-update --quiet 2>/dev/null | grep -c '^\S' || echo 0)
  else
    echo "Сэр, не нашёл пакетного менеджера — пропускаю обновления."
    upd_total=0
  fi
  # убрать не числовые
  upd_total=$(echo "$upd_total" | grep -oE '[0-9]+' | head -1); [ -z "$upd_total" ] && upd_total=0

  if [ "$upd_total" -eq 0 ]; then
    echo "Сэр, обновлений нет — всё свежо, как с иголочки!"
  else
    echo "$(intro "нашёл $upd_total обновлений — ставлю...")"
    if command -v yay >/dev/null 2>&1; then
      yay -Syu --noconfirm 2>&1 | tail -5
      upd_installed=$upd_total
    elif command -v pacman >/dev/null 2>&1; then
      sudo pacman -Syu --noconfirm 2>&1 | tail -5
      upd_installed=$upd_total
    elif command -v apt >/dev/null 2>&1; then
      sudo apt update -qq && sudo apt upgrade -y 2>&1 | tail -5
      upd_installed=$upd_total
    elif command -v dnf >/dev/null 2>&1; then
      sudo dnf upgrade -y 2>&1 | tail -5
      upd_installed=$upd_total
    fi
    echo "Сэр, обновил $upd_installed пакетов — готово."
    # проверка на ядро / reboot-required
    if [ -f /var/run/reboot-required ] || pacman -Q linux 2>/dev/null | grep -q "$(uname -r)" && [ "$upd_installed" -gt 0 ]; then
      # грубая эвристика: если обновлялось ядро
      if pacman -Qtd 2>/dev/null | grep -q .; then :; fi
      reboot_needed=1
    fi
    # если было обновление ядра — точно reboot
    if checkupdates 2>/dev/null | grep -q '^linux '; then reboot_needed=1; fi
  fi
elif [ "$SKIP_UPDATE" -eq 1 ]; then
  echo "$(intro "пропускаю проверку обновлений, сразу к сервисам.")"
else
  echo "$(intro "режим только сервисы — обновления не трогаю.")"
fi

# 2) Перезапуск упавших systemd-сервисов
echo "$(intro "проверяю упавшие сервисы...")"
failed=$(systemctl --failed --no-legend 2>/dev/null | awk '{print $1}')
failed_user=$(systemctl --user --failed --no-legend 2>/dev/null | awk '{print $1}')
all_failed=$(echo -e "$failed\n$failed_user" | sed '/^$/d')
count=$(echo "$all_failed" | grep -c . || true)
[ -z "$all_failed" ] && count=0

if [ "$count" -eq 0 ]; then
  echo "Сэр, упавших сервисов нет — перезапускать нечего."
else
  echo "$(intro "нашёл $count упавших — пробую перезапустить...")"
  ok=0
  fail_list=""
  for unit in $all_failed; do
    [ -z "$unit" ] && continue
    if systemctl --user is-active "$unit" >/dev/null 2>&1; then
      scope="--user"
    else
      scope=""
    fi
    if [ "$scope" = "--user" ]; then
      if systemctl --user restart "$unit" 2>/dev/null; then ok=$((ok+1)); else fail_list="$fail_list $unit"; fi
    else
      if sudo systemctl restart "$unit" 2>/dev/null; then ok=$((ok+1)); else fail_list="$fail_list $unit"; fi
    fi
  done
  if [ "$ok" -eq "$count" ]; then
    echo "Сэр, перезапустил все $ok сервисов."
  elif [ "$ok" -gt 0 ]; then
    echo "Сэр, перезапустил $ok из $count. Не завелись:$fail_list"
  else
    echo "Сэр, ни один из $count не завёлся:$fail_list — нужен ручной взгляд."
  fi
fi

# 3) Перезапуск пользовательских служб Jarvis (если есть)
if systemctl --user list-unit-files "jarvis*" >/dev/null 2>&1; then
  for svc in $(systemctl --user list-unit-files "jarvis*" --no-legend 2>/dev/null | awk '{print $1}'); do
    systemctl --user restart "$svc" 2>/dev/null || true
  done
  echo "Сэр, службы Jarvis перезапустил."
fi

# 4) Перезагрузка если нужно
if [ "$reboot_needed" -eq 1 ]; then
  if [ "$AUTO_REBOOT" -eq 1 ]; then
    echo "Сэр, обновления просят перезагрузку — перезагружаю через 30 секунд!"
    sudo shutdown -r +1 "Jarvis: обновления завершены" 2>/dev/null || sudo reboot
  else
    echo "Сэр, нужна перезагрузка чтобы завершить обновления — скажите 'перезагрузи компьютер' когда будете готовы."
  fi
else
  if [ "$upd_installed" -gt 0 ]; then
    echo "Сэр, всё обновил и перезапустил — перезагрузка не требуется."
  fi
fi
