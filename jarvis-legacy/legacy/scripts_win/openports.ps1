$ErrorActionPreference='SilentlyContinue'
[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false)
Write-Output "Слушающие TCP-порты:"
$i=0
foreach ($c in (Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | Sort-Object LocalPort -Unique | Select-Object -First 15)) {
  $procName = if ($c.OwningProcess) { (Get-Process -Id $c.OwningProcess -ErrorAction SilentlyContinue).ProcessName } else { '?' }
  Write-Output "  Порт $($c.LocalPort) -> $procName"
  $i++
}
if ($i -eq 0) { Write-Output "  Открытых портов нет." }
