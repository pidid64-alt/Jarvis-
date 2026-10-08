$ErrorActionPreference='SilentlyContinue'
[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false)
# Пробуем default браузер напрямую — работает даже если exe не в PATH
try { Start-Process "https://google.com" -ErrorAction Stop; Write-Output "Открываю браузер."; exit 0 } catch {}
foreach ($a in @('msedge','chrome','firefox','brave','opera')) {
  try { Start-Process $a -ErrorAction Stop; Write-Output "Открываю браузер ($a)."; exit 0 } catch {}
}
# fallback через explorer (откроет URL дефолтным браузером)
try { explorer "https://google.com"; Write-Output "Открываю браузер."; exit 0 } catch {}
Write-Output "Браузер не найден."
