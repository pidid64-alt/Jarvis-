$ErrorActionPreference='SilentlyContinue'
[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false)
$profiles = Get-NetFirewallProfile -ErrorAction SilentlyContinue
if (-not $profiles) { Write-Output "Файрвол недоступен (нужны права администратора)."; exit 0 }
$on = @($profiles | Where-Object Enabled).Count
$total = @($profiles).Count
if ($on -eq 0) { Write-Output "Брандмауэр Windows выключен." }
else { Write-Output "Брандмауэр Windows включен, активных профилей: $on из $total." }
