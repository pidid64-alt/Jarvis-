$ErrorActionPreference='SilentlyContinue'
[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false)
Add-Type -AssemblyName System.Windows.Forms
$screens = [System.Windows.Forms.Screen]::AllScreens
Write-Output "Подключено мониторов: $($screens.Count)."
$primary = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
Write-Output "Разрешение основного экрана: $($primary.Width) на $($primary.Height)."
