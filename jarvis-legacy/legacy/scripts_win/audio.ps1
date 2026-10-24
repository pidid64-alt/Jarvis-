$ErrorActionPreference='SilentlyContinue'
[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false)
$devices = @(Get-CimInstance Win32_SoundDevice -ErrorAction SilentlyContinue)
Write-Output "Аудио устройств: $($devices.Count)."
foreach ($d in $devices | Select-Object -First 3) { Write-Output "  $($d.Name)" }
