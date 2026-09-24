$ErrorActionPreference='SilentlyContinue'
[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false)
foreach ($a in @('msedge','chrome','firefox','brave','opera')) {
  $cmd = Get-Command $a -ErrorAction SilentlyContinue
  if ($cmd) { Start-Process $a; Write-Output "Открываю браузер ($a)."; exit 0 }
}
Write-Output "Браузер не найден."
