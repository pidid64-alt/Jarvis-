param([string]$Action = 'list')
$ErrorActionPreference='SilentlyContinue'
[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false)
if ($Action -eq 'eject') {
  $disk = Get-CimInstance Win32_DiskDrive -ErrorAction SilentlyContinue |
          Where-Object { $_.InterfaceType -eq 'USB' } | Select-Object -First 1
  if (-not $disk) { Write-Output "USB-устройство не найдено."; exit 0 }
  $parts = Get-Partition -DiskNumber $disk.Index -ErrorAction SilentlyContinue |
           Where-Object DriveLetter
  if (-not $parts) { Write-Output "У устройства нет букв дисков."; exit 0 }
  foreach ($p in $parts) {
    $letter = [string]$p.DriveLetter
    mountvol "${letter}:" /d 2>$null
    Write-Output "Извлекаю диск ${letter}:."
  }
  exit 0
}
$usb = @(Get-CimInstance Win32_DiskDrive -ErrorAction SilentlyContinue | Where-Object InterfaceType -eq 'USB')
Write-Output "Подключено дисков USB: $($usb.Count)."
foreach ($d in $usb) { Write-Output "  $($d.Model)" }
