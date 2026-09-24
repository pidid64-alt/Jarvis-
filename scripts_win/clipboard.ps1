param([string]$Action = 'read')
$ErrorActionPreference='SilentlyContinue'
[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false)
function Get-Clip { (Get-Clipboard -Raw -ErrorAction SilentlyContinue) }
function Set-Clip($v) { Set-Clipboard -Value $v }
switch ($Action) {
  'read' {
    $t = Get-Clip
    if (-not $t) { Write-Output "Буфер обмена пуст."; exit 0 }
    $t = ($t -replace '\s+',' ').Trim()
    if ($t.Length -gt 200) { $t = $t.Substring(0,200) + '…' }
    Write-Output $t
  }
  'clear' { Set-Clip ''; Write-Output "Буфер обмена очищен." }
  'save' {
    $name = "clipboard_{0}.txt" -f (Get-Date -Format 'yyyyMMdd_HHmmss')
    $path = Join-Path $env:USERPROFILE $name
    Set-Content -Path $path -Value (Get-Clip) -Encoding UTF8
    Write-Output "Сохранено в $name."
  }
  'count_chars' {
    $t = Get-Clip; if (-not $t) { $t='' }
    Write-Output "Символов в буфере: $($t.Length)."
  }
  'count_words' {
    $t = Get-Clip; if (-not $t) { $t='' }
    $n = @($t -split '\s+' | Where-Object { $_ }).Count
    Write-Output "Слов в буфере: $n."
  }
  'to_uppercase' { $t = Get-Clip; if ($t) { Set-Clip ($t.ToUpper()) }; Write-Output "Преобразовано в верхний регистр." }
  'to_lowercase' { $t = Get-Clip; if ($t) { Set-Clip ($t.ToLower()) }; Write-Output "Преобразовано в нижний регистр." }
}
