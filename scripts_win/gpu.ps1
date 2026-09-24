$ErrorActionPreference='SilentlyContinue'
[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false)
$g = Get-CimInstance Win32_VideoController | Select-Object -First 1
if (-not $g) { Write-Output "Видеокарта не найдена."; exit 0 }
Write-Output "Видеокарта: $($g.Name)."
$util = & nvidia-smi --query-gpu=utilization.gpu --format=csv,noheader,nounits 2>$null
if ($util) { Write-Output "Загрузка GPU: $util процентов." }
$temp = & nvidia-smi --query-gpu=temperature.gpu --format=csv,noheader 2>$null
if ($temp) { Write-Output "Температура GPU: $temp градусов." }
