$ErrorActionPreference='SilentlyContinue'
[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false)
$os=Get-CimInstance Win32_OperatingSystem
$total=[math]::Round($os.TotalVisibleMemorySize/1MB,1)
$free=[math]::Round($os.FreePhysicalMemory/1MB,1)
$used=[math]::Round($total-$free,1)
Write-Output "Используется $used из $total гигабайт оперативной памяти."
