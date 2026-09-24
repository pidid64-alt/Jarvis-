param([Parameter(Mandatory=$true)][string]$Action)
$ErrorActionPreference='SilentlyContinue'
[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false)
Add-Type -AssemblyName System.Windows.Forms
Add-Type @'
using System;
using System.Runtime.InteropServices;
public class WinCtl {
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr hWnd, out RECT rect);
  [DllImport("user32.dll")] public static extern bool MoveWindow(IntPtr hWnd, int X, int Y, int W, int H, bool repaint);
  [DllImport("user32.dll")] public static extern int GetWindowLong(IntPtr hWnd, int nIndex);
  [DllImport("user32.dll")] public static extern int SetWindowLong(IntPtr hWnd, int nIndex, int dwNewLong);
  public struct RECT { public int Left, Top, Right, Bottom; }
}
'@
$fg = Get-Process -ErrorAction SilentlyContinue |
      Where-Object { $_.MainWindowHandle -ne 0 -and $_.ProcessName -notmatch '^(powershell|cmd|python)$' } |
      Sort-Object StartTime -Descending | Select-Object -First 1
if (-not $fg) { Write-Output "Нет активного окна."; exit 0 }
$hwnd = $fg.MainWindowHandle
$bounds = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
$sw = $bounds.Width; $sh = $bounds.Height
$msg=''
switch ($Action) {
  'tile_left'  { [WinCtl]::MoveWindow($hwnd, 0, 0, [int]($sw/2), $sh, $true) | Out-Null; $msg='Прижато влево' }
  'tile_right' { [WinCtl]::MoveWindow($hwnd, [int]($sw/2), 0, [int]($sw/2), $sh, $true) | Out-Null; $msg='Прижато вправо' }
  'center' {
    $w=[int]($sw*0.7); $h=[int]($sh*0.8)
    [WinCtl]::MoveWindow($hwnd, [int](($sw-$w)/2), [int](($sh-$h)/2), $w, $h, $true) | Out-Null
    $msg='Отцентровано'
  }
  'minimize'   { [WinCtl]::ShowWindow($hwnd, 6) | Out-Null; $msg='Свернуто' }
  'maximize'   { [WinCtl]::ShowWindow($hwnd, 3) | Out-Null; $msg='Развёрнуто' }
  'restore'    { [WinCtl]::ShowWindow($hwnd, 9) | Out-Null; $msg='Восстановлено' }
  'always_on_top' {
    $ex = [WinCtl]::GetWindowLong($hwnd, -20)
    [WinCtl]::SetWindowLong($hwnd, -20, ($ex -bxor 0x8)) | Out-Null
    $msg='Переключено поверх всех окон'
  }
  'fullscreen' {
    [WinCtl]::ShowWindow($hwnd, 3) | Out-Null
    $msg='Окно развёрнуто на весь экран'
  }
  'close' {
    [WinCtl]::ShowWindow($hwnd, 6) | Out-Null
    $msg='Окно свёрнуто'
  }
}
if ($Action -eq 'active_title') {
  $t = $fg.MainWindowTitle
  if ($t) { Write-Output "Активное окно: $t." } else { Write-Output "Название окна недоступно." }
  exit 0
}
if ($msg) { Write-Output "$msg." }
