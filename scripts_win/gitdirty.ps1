$ErrorActionPreference='SilentlyContinue'
[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false)
$root = $env:JARVIS_GIT_SEARCH_DIRS
if (-not $root) { $root = $env:USERPROFILE }
$count=0; $names=@()
foreach ($base in ($root -split ';')) {
  if (-not (Test-Path $base)) { continue }
  foreach ($g in (Get-ChildItem -Path $base -Directory -Depth 3 -Force -ErrorAction SilentlyContinue | Where-Object Name -eq '.git')) {
    $repo=$g.Parent.FullName
    $status = & git -C $repo status --porcelain 2>$null
    if ($status) { $count++; $names += (Split-Path $repo -Leaf) }
  }
}
if ($count -eq 0) { Write-Output "Незакоммиченных изменений не нашёл." }
else { Write-Output ("Грязных репозиториев: $count. Это " + ($names -join ', ') + ".") }
