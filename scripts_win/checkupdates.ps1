$ErrorActionPreference='SilentlyContinue'
[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false)
$n = 0
try {
  $out = & winget upgrade --accept-source-agreements --disable-interactivity 2>$null
  $n = @($out | Where-Object { $_ -match '^\S+\s+\S+' -and $_ -notmatch '^\s*-+' -and $_ -notmatch 'ID\s+Name|доступно|upgrades available|no applicable' }).Count
} catch { $n = 0 }
if ($n -le 0) { Write-Output "Обновлений нет." } else { Write-Output "Доступно обновлений: $n." }
