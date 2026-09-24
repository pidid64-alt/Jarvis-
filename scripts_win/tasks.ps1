$ErrorActionPreference='SilentlyContinue'
[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false)
$tasks = @(Get-ScheduledTask -ErrorAction SilentlyContinue | Where-Object { $_.State -eq 'Ready' } | Select-Object -First 5)
if (-not $tasks) { Write-Output "Активных задач расписания нет."; exit 0 }
Write-Output "Ближайшие задачи расписания:"
foreach ($t in $tasks) { Write-Output "  $($t.TaskName)" }
