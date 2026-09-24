$ErrorActionPreference='SilentlyContinue'
[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false)
$rows = @(query user 2>$null)
if ($rows.Count -le 1) { Write-Output "Сейчас в системе только этот сеанс."; exit 0 }
$names = @()
foreach ($r in ($rows | Select-Object -Skip 1)) {
  $n = ($r -split '\s+')[0]
  if ($n) { $names += $n }
}
Write-Output ("Сейчас в системе: " + ($names -join ', ') + ".")
