$ErrorActionPreference='SilentlyContinue'
[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false)
$InfoType = if ($args.Count -gt 0) { $args[0] } else { '' }
switch ($InfoType) {
  'cpu_model' {
    $c = Get-CimInstance Win32_Processor | Select-Object -First 1
    Write-Output $c.Name.Trim()
  }
  'total_memory' {
    $os = Get-CimInstance Win32_OperatingSystem
    Write-Output ("{0} гигабайт" -f [math]::Round($os.TotalVisibleMemorySize/1MB,1))
  }
  'arch' { Write-Output $env:PROCESSOR_ARCHITECTURE }
  'desktop' { Write-Output "Windows (рабочий стол Explorer)" }
  'os' {
    $os = Get-CimInstance Win32_OperatingSystem
    Write-Output $os.Caption
  }
}
