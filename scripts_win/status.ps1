$ErrorActionPreference='SilentlyContinue'
[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false)
$boot=(Get-CimInstance Win32_OperatingSystem).LastBootUpTime
$up=New-TimeSpan -Start $boot
if ($up.Days -gt 0){ $ups="$($up.Days) дн. $($up.Hours) ч." } elseif ($up.Hours -gt 0){ $ups="$($up.Hours) ч. $($up.Minutes) мин." } else { $ups="$($up.Minutes) мин." }
$os=Get-CimInstance Win32_OperatingSystem
$memTotal=[math]::Round($os.TotalVisibleMemorySize/1MB,1)
$memFree=[math]::Round($os.FreePhysicalMemory/1MB,1)
$memUsed=[math]::Round($memTotal-$memFree,1)
$cpu=(Get-Process | Sort-Object CPU -Descending | Select-Object -First 1).ProcessName
Write-Output "Время работы: $ups. Память занята: $memUsed из $memTotal гигабайт. Больше всего грузит процессор: $cpu."
