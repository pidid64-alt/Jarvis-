$ErrorActionPreference='SilentlyContinue'
[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false)
$mp = Get-MpComputerStatus -ErrorAction SilentlyContinue
if (-not $mp) { Write-Output "Защитник Windows недоступен."; exit 0 }
if ($mp.AntivirusEnabled) { Write-Output "Защитник Windows включен, сигнатуры свежие: $($mp.AntivirusSignatureAge) дн." }
else { Write-Output "Защитник Windows выключен." }
