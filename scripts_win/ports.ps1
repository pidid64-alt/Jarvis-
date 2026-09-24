$ErrorActionPreference='SilentlyContinue'
[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false)
$count = @(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue).Count
Write-Output "Открытых портов: $count."
