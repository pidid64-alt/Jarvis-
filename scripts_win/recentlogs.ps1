$ErrorActionPreference='SilentlyContinue'
[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false)
try {
  $boot = (Get-CimInstance Win32_OperatingSystem).LastBootUpTime
  $events = @(Get-WinEvent -FilterHashtable @{LogName='System'; Level=1,2; StartTime=$boot} -MaxEvents 50 -ErrorAction SilentlyContinue)
} catch { $events = @() }
if ($events.Count -eq 0) { Write-Output "Критичных ошибок в логе этой загрузки нет."; exit 0 }
$total=$events.Count
$show=[math]::Min($total,5)
if ($total -gt $show) { Write-Output "Критичных ошибок в логе: $total. Вот последние $show:" }
elseif ($total -eq 1) { Write-Output "В логе этой загрузки одна критичная запись:" }
else { Write-Output "Критичных ошибок в логе: $total:" }
foreach ($e in $events | Select-Object -First $show) {
  $msg = ($e.Message -replace '\s+',' ').Trim()
  if ($msg.Length -gt 140) { $msg = $msg.Substring(0,140) }
  Write-Output "$($e.TimeCreated.ToString('dd.MM HH:mm')): $($e.ProviderName): $msg"
}
