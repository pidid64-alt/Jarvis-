$ErrorActionPreference='SilentlyContinue'
[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false)
$boot=(Get-CimInstance Win32_OperatingSystem).LastBootUpTime
$up=New-TimeSpan -Start $boot
if ($up.Days -gt 0){ $ups="$($up.Days) дн." } elseif ($up.Hours -gt 0){ $ups="$($up.Hours) ч." } else { $ups="$($up.Minutes) мин." }
Write-Output "Последняя загрузка: $($boot.ToString('dd.MM.yyyy HH:mm')). Работаем уже $ups."
