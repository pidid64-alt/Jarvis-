param([string]$Action = 'count_recent')
$ErrorActionPreference='SilentlyContinue'
[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false)
$home_ = $env:USERPROFILE
switch ($Action) {
  'count_recent' {
    $n = @(Get-ChildItem -Path $home_ -File -Recurse -Force -ErrorAction SilentlyContinue |
           Where-Object { $_.LastWriteTime -gt (Get-Date).AddDays(-1) }).Count
    Write-Output "Файлов изменено за последние сутки: $n."
  }
  'count_files' {
    $n = @(Get-ChildItem -Path $home_ -File -Recurse -Force -ErrorAction SilentlyContinue).Count
    Write-Output "Файлов в домашней папке: $n."
  }
  'count_dirs' {
    $n = @(Get-ChildItem -Path $home_ -Directory -Recurse -Force -ErrorAction SilentlyContinue).Count
    Write-Output "Папок в домашней папке: $n."
  }
  'size_downloads' {
    $s = (Get-ChildItem -Path (Join-Path $home_ 'Downloads') -File -Recurse -Force -ErrorAction SilentlyContinue |
          Measure-Object -Property Length -Sum).Sum
    Write-Output ("Загрузки занимают {0} гигабайт." -f [math]::Round($s/1GB,2))
  }
  'size_home' {
    $s = (Get-ChildItem -Path $home_ -File -Recurse -Force -ErrorAction SilentlyContinue |
          Measure-Object -Property Length -Sum).Sum
    Write-Output ("Домашняя папка занимает {0} гигабайт." -f [math]::Round($s/1GB,2))
  }
  'size_temp' {
    $s = (Get-ChildItem -Path $env:TEMP -File -Recurse -Force -ErrorAction SilentlyContinue |
          Measure-Object -Property Length -Sum).Sum
    Write-Output ("Временные файлы занимают {0} гигабайт." -f [math]::Round($s/1GB,2))
  }
  'large_files' {
    $files = Get-ChildItem -Path $home_ -File -Recurse -Force -ErrorAction SilentlyContinue |
             Where-Object Length -gt 100MB | Sort-Object Length -Descending | Select-Object -First 5
    if (-not $files) { Write-Output "Больших файлов более 100 мегабайт не найдено."; exit 0 }
    Write-Output "Самые большие файлы:"
    foreach ($f in $files) { Write-Output ("{0}: {1} мегабайт." -f $f.Name, [int]($f.Length/1MB)) }
  }
}
