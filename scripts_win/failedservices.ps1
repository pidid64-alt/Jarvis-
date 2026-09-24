$ErrorActionPreference='SilentlyContinue'
[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false)
$failed = Get-Service | Where-Object { $_.StartType -eq 'Automatic' -and $_.Status -ne 'Running' }
if (-not $failed) { Write-Output "Сломанных сервисов нет, всё работает штатно."; exit 0 }
Write-Output "Упавших сервисов: $($failed.Count)."
foreach ($s in $failed) { Write-Output "Сервис $($s.Name) (системный, состояние $($s.Status)): автоматический запуск, но не работает." }
