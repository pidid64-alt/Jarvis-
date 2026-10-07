param([int]$Minutes = 5, [string]$Message = 'Время вышло!')
$ErrorActionPreference='SilentlyContinue'
[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false)
# Таймер: порождаем отдельный скрытый powershell, который уснёт и покажет тост.
$script = @'
param($Min, $Msg)
Start-Sleep -Seconds ($Min * 60)
$dir = Join-Path $env:LOCALAPPDATA 'Jarvis'
[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false)
& powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $dir 'notify_toast.ps1') -Title 'Таймер' -Body $Msg -Urgency 'normal'
'@
$tmp = Join-Path $env:TEMP ('jarvis_timer_' + [guid]::NewGuid().ToString('N') + '.ps1')
Set-Content -Path $tmp -Value $script -Encoding UTF8
Start-Process powershell -WindowStyle Hidden -ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-File',$tmp,'-Min',$Minutes,'-Msg',$Message)
Write-Output "Таймер на $Minutes минут запущен."
