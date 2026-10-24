$ErrorActionPreference='SilentlyContinue'
[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false)
$gw = (Get-NetRoute -DestinationPrefix '0.0.0.0/0' -ErrorAction SilentlyContinue | Select-Object -First 1).NextHop
if (-not $gw) { Write-Output "Шлюз не найден."; exit 0 }
$ok = Test-Connection -ComputerName $gw -Count 2 -Quiet -ErrorAction SilentlyContinue
if ($ok) { Write-Output "Связь с роутером есть." } else { Write-Output "Роутер не отвечает." }
