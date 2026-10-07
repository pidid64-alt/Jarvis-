$ErrorActionPreference='SilentlyContinue'
[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false)
$ip=(Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -notlike '127.*' -and $_.PrefixOrigin -ne 'WellKnown' } | Select-Object -First 1).IPAddress
if (-not $ip){ Write-Output "Не вижу активного сетевого подключения."; exit 0 }
$spoken=$ip -replace '\.',' точка '
Write-Output "Локальный адрес: $spoken."
