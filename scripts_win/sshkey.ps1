$ErrorActionPreference='SilentlyContinue'
[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false)
$dir = Join-Path $env:USERPROFILE '.ssh'
$pub = @(Get-ChildItem -Path $dir -Filter '*.pub' -ErrorAction SilentlyContinue)
if (-not $pub) { Write-Output "SSH ключей не нашёл."; exit 0 }
$pub | ForEach-Object { Write-Output $_.Name }
