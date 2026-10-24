param([string]$Action = 'status')
$ErrorActionPreference='SilentlyContinue'
[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false)
switch ($Action) {
  'wifi_list' {
    $nets = @(netsh wlan show networks)
    $count = @($nets | Where-Object { $_ -match 'SSID\s+\d+' }).Count
    Write-Output "Видно сетей вайфай: $count."
  }
  'wifi_ssid' {
    $line = netsh wlan show interfaces | Select-String 'SSID' | Select-Object -First 1
    if ($line) { Write-Output ("Подключен к сети: " + ($line.ToString() -replace '.*SSID\s*:\s*','')) }
    else { Write-Output "Не подключен к вайфай." }
  }
  'wifi_signal' {
    $line = netsh wlan show interfaces | Select-String 'Signal' | Select-Object -First 1
    if ($line) { Write-Output ("Сигнал WiFi: " + ($line.ToString() -replace '.*:\s*','')) }
    else { Write-Output "Сигнал WiFi недоступен." }
  }
  'wifi_off' {
    Get-NetAdapter -Physical -ErrorAction SilentlyContinue |
      Where-Object { $_.InterfaceDescription -match 'Wi-?Fi|Wireless|802\.11|WLAN' } |
      Disable-NetAdapter -Confirm:$false -ErrorAction SilentlyContinue
    Write-Output "Вайфай выключен."
  }
  'wifi_on' {
    Get-NetAdapter -Physical -ErrorAction SilentlyContinue |
      Where-Object { $_.InterfaceDescription -match 'Wi-?Fi|Wireless|802\.11|WLAN' } |
      Enable-NetAdapter -Confirm:$false -ErrorAction SilentlyContinue
    Write-Output "Вайфай включен."
  }
  'restart_network' {
    Get-NetAdapter -Physical -ErrorAction SilentlyContinue | Restart-NetAdapter -Confirm:$false
    Write-Output "Сетевой адаптер перезапущен."
  }
  'local_ips' {
    $ips = @(Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -notlike '127.*' } | ForEach-Object { $_.IPAddress })
    Write-Output ("Локальные IP: " + ($ips -join ', ') + ".")
  }
  'connections' {
    $n = @(Get-NetTCPConnection -State Established -ErrorAction SilentlyContinue).Count
    Write-Output "Активных соединений: $n."
  }
  'internet_off' {
    Get-NetAdapter -Physical -ErrorAction SilentlyContinue | Disable-NetAdapter -Confirm:$false -ErrorAction SilentlyContinue
    Write-Output "Интернет отключён."
  }
  'internet_on' {
    Get-NetAdapter -Physical -ErrorAction SilentlyContinue | Enable-NetAdapter -Confirm:$false -ErrorAction SilentlyContinue
    Write-Output "Интернет включён."
  }
  'ping_latency' {
    $r = Test-Connection -ComputerName 1.1.1.1 -Count 3 -ErrorAction SilentlyContinue
    if ($r) {
      $avg = [math]::Round(($r | Measure-Object -Property ResponseTime -Average).Average, 1)
      Write-Output "Средняя задержка: $avg мс."
    } else { Write-Output "Сервер недоступен." }
  }
}
