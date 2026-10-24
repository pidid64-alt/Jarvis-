#!/bin/bash
# Детальный отчёт по упавшим systemd-юнитам: что, когда и почему.
# Формат рассчитан на озвучку TTS (короткие фразы) — цифры нормализует tts_norm.

user_failed=$(systemctl --user --failed --no-legend 2>/dev/null | awk '{print $2, $4}')
sys_failed=$(systemctl --failed --no-legend 2>/dev/null | awk '{print $2, $4}')

count_user=$(echo "$user_failed" | grep -c . )
[ -z "$user_failed" ] && count_user=0
count_sys=$(echo "$sys_failed" | grep -c .)
[ -z "$sys_failed" ] && count_sys=0
total=$((count_user + count_sys))

if [ "$total" -eq 0 ]; then
  echo "Сломанных сервисов нет, всё работает штатно."
  exit 0
fi

echo "Упавших сервисов: ${total}."

report_unit() {
  local scope="$1" unit="$2" sub="$3"
  local when_str reason_str

  # Когда упал: timestamp перехода в failed/ inactive.
  if [ "$scope" = "пользовательский" ]; then
    since=$(systemctl --user show "$unit" -p InactiveEnterTimestamp --value 2>/dev/null)
    [ -z "$since" ] && since=$(systemctl --user show "$unit" -p ActiveEnterTimestamp --value 2>/dev/null)
    result=$(systemctl --user show "$unit" -p Result --value 2>/dev/null)
    code=$(systemctl --user show "$unit" -p ExecMainStatus --value 2>/dev/null)
  else
    since=$(systemctl show "$unit" -p InactiveEnterTimestamp --value 2>/dev/null)
    [ -z "$since" ] && since=$(systemctl show "$unit" -p ActiveEnterTimestamp --value 2>/dev/null)
    result=$(systemctl show "$unit" -p Result --value 2>/dev/null)
    code=$(systemctl show "$unit" -p ExecMainStatus --value 2>/dev/null)
  fi

  [ -n "$since" ] && when_str="Упал ${since}." || when_str="Время падения неизвестно."
  # "Mon 2026-08-24 16:35:36 UTC" -> убрать день недели для краткости речи.
  when_str=$(echo "$when_str" | sed -E 's/Упал [A-Za-zА-Яа-я]{2,3} /Упал /')

  # Причина: последние сообщения об ошибке из журнала этого юнита.
  reason_str=""
  if [ "$scope" = "пользовательский" ]; then
    log_line=$(journalctl --user -u "$unit" -p err -n 3 --no-pager --output=short-iso 2>/dev/null | tail -1)
  else
    log_line=$(journalctl -u "$unit" -p err -n 3 --no-pager --output=short-iso 2>/dev/null | tail -1)
  fi
  if [ -n "$log_line" ]; then
    log_msg=$(echo "$log_line" | sed 's/^[^ ]*T[^ ]* //' | sed -E 's/^([^ ]+ )?[^ ]+\[[0-9]+\]: //' | cut -c1-160)
    [ -n "$log_msg" ] && reason_str="Причина: ${log_msg%.}."
  fi

  case "${result:-}" in
    exit-code) why="процесс завершился с кодом ошибки ${code}";;
    signal)    why="процесс убит сигналом";;
    timeout)   why="превышено время ожидания запуска или остановки";;
    oom-kill)  why="убит из-за нехватки памяти";;
    resources) why="ошибка выделения ресурсов";;
    *)         why="причина: ${result:-неизвестна}";;
  esac

  echo "Сервис ${unit} (${scope}, состояние ${sub}): ${why}. ${when_str} ${reason_str}"
}

# Пользовательские юниты
if [ "$count_user" -gt 0 ]; then
  while read -r unit sub; do
    [ -z "$unit" ] && continue
    report_unit "пользовательский" "$unit" "$sub"
  done <<< "$user_failed"
fi

# Системные юниты
if [ "$count_sys" -gt 0 ]; then
  while read -r unit sub; do
    [ -z "$unit" ] && continue
    report_unit "системный" "$unit" "$sub"
  done <<< "$sys_failed"
fi
