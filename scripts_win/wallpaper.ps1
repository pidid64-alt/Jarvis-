$ErrorActionPreference='SilentlyContinue'
[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false)
$files = @(Get-ChildItem -Path (Join-Path $env:USERPROFILE 'Pictures') -Include *.jpg,*.png -File -Recurse -ErrorAction SilentlyContinue)
if (-not $files) { Write-Output "Картинок для обоев не нашёл."; exit 0 }
$pick = $files | Get-Random
Add-Type -Namespace Win32 -Name Style -MemberDefinition @'
[DllImport("user32.dll", CharSet=CharSet.Auto)]
public static extern int SystemParametersInfo(int uAction, int uParam, string lpvParam, int fuWinIni);
'@
[Win32.Style]::SystemParametersInfo(0x0014, 0, $pick.FullName, 0x0001 -bor 0x0002) | Out-Null
Write-Output "Меняю обои."
