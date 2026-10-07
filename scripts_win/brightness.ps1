param([string]$Action = 'up')
$ErrorActionPreference='SilentlyContinue'
[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false)
$cur = $null
try {
  $cur = (Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightness -ErrorAction Stop | Select-Object -First 1).CurrentBrightness
} catch { $cur = $null }
if ($null -eq $cur) { Write-Output "Яркость не поддерживается на этом экране."; exit 0 }
if ($Action -eq 'down') { $new=[math]::Max(10, $cur-10) } else { $new=[math]::Min(100, $cur+10) }
try {
  $m = Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightnessMethods -ErrorAction Stop | Select-Object -First 1
  $m | Invoke-CimMethod -MethodName WmiSetBrightness -Arguments @{Timeout=0; Brightness=[byte]$new} | Out-Null
  Write-Output "Яркость: $new процентов."
} catch {
  Write-Output "Не удалось изменить яркость (нужны права администратора)."
}
