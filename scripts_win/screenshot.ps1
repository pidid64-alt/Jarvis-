param([string]$Mode = 'full')
$ErrorActionPreference='SilentlyContinue'
[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false)
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$dir = Join-Path $env:USERPROFILE 'Pictures\Screenshots'
New-Item -ItemType Directory -Force -Path $dir | Out-Null
if ($Mode -eq 'selection') {
  # Обрезка области: Win+Shift+S, снимок попадает в буфер обмена
  [System.Windows.Forms.SendKeys]::SendWait('^+{PRTSC}')
  Write-Output "Выдели область на экране, снимок окажется в буфере обмена."
  exit 0
}
$b=[System.Windows.Forms.Screen]::PrimaryScreen.Bounds
$bmp=New-Object System.Drawing.Bitmap $b.Width, $b.Height
$g=[System.Drawing.Graphics]::FromImage($bmp)
$g.CopyFromScreen($b.Location, [System.Drawing.Point]::Empty, $b.Size)
$name = Join-Path $dir ("screenshot_{0}.png" -f (Get-Date -Format 'yyyyMMdd_HHmmss'))
$bmp.Save($name, [System.Drawing.Imaging.ImageFormat]::Png)
$g.Dispose(); $bmp.Dispose()
Write-Output "Снимок сохранён в Pictures Screenshots."
