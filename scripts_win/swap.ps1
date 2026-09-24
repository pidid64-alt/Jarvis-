$ErrorActionPreference='SilentlyContinue'
[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false)
# Подкачка (файл подкачки) через WMI
$pages = @(Get-CimInstance Win32_PageFileUsage -ErrorAction SilentlyContinue)
if (-not $pages) { Write-Output "Файл подкачки не найден."; exit 0 }
foreach ($p in $pages) {
  $alloc = [math]::Round($p.AllocatedBaseSize/1024,1)
  $used = [math]::Round($p.CurrentUsage/1024,1)
  Write-Output "Подкачка: $used из $alloc гигабайт."
}
