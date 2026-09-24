$ErrorActionPreference='SilentlyContinue'
[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false)
$parts=@()
foreach ($d in (Get-CimInstance Win32_LogicalDisk -Filter "DriveType=3")) {
  $free=[math]::Round($d.FreeSpace/1GB,1); $total=[math]::Round($d.Size/1GB,1)
  $pct=[math]::Round($free/$total*100,1)
  $parts += "На диске $($d.DeviceID) свободно $pct процентов."
}
if (-not $parts){ Write-Output "Диски недоступны." } else { Write-Output ($parts -join ' ') }
