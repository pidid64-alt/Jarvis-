$ErrorActionPreference='SilentlyContinue'
[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false)
$top=Get-Process | Sort-Object CPU -Descending | Select-Object -First 3
$parts=@(); foreach ($p in $top){ $parts += "$($p.ProcessName): загрузка процессора." }
Write-Output ("Больше всего грузят: " + ($parts -join ' '))
