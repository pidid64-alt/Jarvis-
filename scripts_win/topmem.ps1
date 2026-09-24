$ErrorActionPreference='SilentlyContinue'
[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false)
$top=Get-Process | Sort-Object WS -Descending | Select-Object -First 5
$i=1; foreach ($p in $top){
  $mb=[math]::Round($p.WS/1MB,1)
  Write-Output "$i. $($p.ProcessName) — $mb мегабайт."
  $i++
}
