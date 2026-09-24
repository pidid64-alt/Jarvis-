$ErrorActionPreference='SilentlyContinue'
[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false)
# Считаем только обычные Auto (не Delayed) — Delayed-Auto стартуют по триггеру и
# нормально висят Stopped. Плюс фильтруем заведомо фоновые апдейтеры/телеметрию,
# которые на чистой Windows всегда Stopped, но StartMode=Auto.
$ignore = '^(edgeupdate|edgeupdatem|GoogleUpdater|gupdate|Intel.*TPM|MapsBroker|qcmtsuvc|SCardSvr|sppsvc)'
$failed = Get-CimInstance Win32_Service -ErrorAction SilentlyContinue | Where-Object {
  $_.StartMode -eq 'Auto' -and $_.State -ne 'Running' -and -not $_.DelayedAutoStart -and $_.Name -notmatch $ignore
}
if (-not $failed) { Write-Output "Сломанных сервисов нет, всё работает штатно."; exit 0 }
Write-Output "Упавших сервисов: $($failed.Count)."
foreach ($s in $failed) { Write-Output "Сервис $($s.Name) ($($s.DisplayName), состояние $($s.State)): автоматический запуск, но не работает." }
