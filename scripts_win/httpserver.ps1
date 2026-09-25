param([string]$Action = 'start')
$ErrorActionPreference='SilentlyContinue'
[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false)
if ($Action -eq 'start') {
  Start-Process python -ArgumentList '-m','http.server','8000' -WorkingDirectory $env:USERPROFILE -WindowStyle Hidden
  Write-Output "Запускаю HTTP сервер на порту 8000."
} else {
  $conns = @(Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue)
  foreach ($c in $conns) { Stop-Process -Id $c.OwningProcess -Force -ErrorAction SilentlyContinue }
  Write-Output "HTTP сервер остановлен."
}
