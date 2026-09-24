$ErrorActionPreference='SilentlyContinue'
[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false)
$dns = (Get-DnsClientServerAddress -AddressFamily IPv4 | Where-Object { $_.ServerAddresses } | Select-Object -First 1).ServerAddresses
if (-not $dns) {
  $dns = (Select-String -Path "$env:SystemRoot\System32\drivers\etc\hosts" -Pattern '^nameserver' -ErrorAction SilentlyContinue)
}
if (-not $dns) { Write-Output "Не нашёл настроенный ДНС."; exit 0 }
$first = @($dns)[0]
$spoken = $first -replace '\.',' точка '
Write-Output "ДНС-сервер: $spoken."
