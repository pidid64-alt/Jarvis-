$ErrorActionPreference='SilentlyContinue'
[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false)
$os=Get-CimInstance Win32_OperatingSystem
$cpu=Get-CimInstance Win32_Processor | Select-Object -First 1
$boot=$os.LastBootUpTime
$up=New-TimeSpan -Start $boot
Write-Output "Хост: $($env:COMPUTERNAME)."
Write-Output "Время работы: $($up.Days) дн. $($up.Hours) ч."
Write-Output "Процессор: $($cpu.Name.Trim())."
Write-Output "Загрузка: $([math]::Round($cpu.LoadPercentage,0)) процентов."
$total=[math]::Round($os.TotalVisibleMemorySize/1MB,1)
$free=[math]::Round($os.FreePhysicalMemory/1MB,1)
Write-Output "Память: занято $([math]::Round($total-$free,1)) из $total гигабайт."
foreach ($d in (Get-CimInstance Win32_LogicalDisk -Filter 'DriveType=3')) {
  $pct=[math]::Round(($d.Size-$d.FreeSpace)/$d.Size*100,0)
  Write-Output "Диск $($d.DeviceID): занято $pct процентов."
}
$ip=(Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -notlike '127.*' } | Select-Object -First 1).IPAddress
Write-Output "Сеть: $ip."
