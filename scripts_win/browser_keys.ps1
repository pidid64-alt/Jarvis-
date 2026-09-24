param([string]$Action = 'next')
$ErrorActionPreference='SilentlyContinue'
[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false)
Add-Type @'
using System;
using System.Runtime.InteropServices;
public class Win32Focus {
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
}
'@
# Ищем окно браузера (Edge/Chrome/Firefox); переключаемся на него и шлём Ctrl-команды.
$names = @('msedge','chrome','firefox','brave','opera')
$proc = Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowTitle -and ($names -contains $_.ProcessName) } | Select-Object -First 1
if (-not $proc) { Write-Output "Не нашёл открытое окно браузера."; exit 0 }
[Win32Focus]::ShowWindow($proc.MainWindowHandle, 9) | Out-Null
[Win32Focus]::SetForegroundWindow($proc.MainWindowHandle) | Out-Null
Start-Sleep -Milliseconds 300
$wsh = New-Object -ComObject WScript.Shell
$msg = ''
switch ($Action) {
  'next'     { $wsh.SendKeys('^{TAB}');      $msg='Следующая вкладка' }
  'prev'     { $wsh.SendKeys('^+{TAB}');     $msg='Предыдущая вкладка' }
  'close'    { $wsh.SendKeys('^w');          $msg='Вкладка закрыта' }
  'new'      { $wsh.SendKeys('^t');          $msg='Новая вкладка' }
  'reopen'   { $wsh.SendKeys('^+t');         $msg='Восстановлена вкладка' }
  'refresh'  { $wsh.SendKeys('{F5}');        $msg='Обновлено' }
  'zoom_in'  { $wsh.SendKeys('^+');          $msg='Увеличено' }
  'zoom_out' { $wsh.SendKeys('^-');          $msg='Уменьшено' }
  'zoom_reset'{ $wsh.SendKeys('^0');         $msg='Сброшено' }
  'back'     { $wsh.SendKeys('%{LEFT}');     $msg='Возвращаюсь назад' }
  'forward'  { $wsh.SendKeys('%{RIGHT}');    $msg='Вперёд' }
  'history'  { $wsh.SendKeys('^h');          $msg='Открываю историю' }
  'downloads'{ $wsh.SendKeys('^j');          $msg='Открываю загрузки' }
  'find'     { $wsh.SendKeys('^f');          $msg='Открываю поиск на странице' }
  'fullscreen'{ $wsh.SendKeys('{F11}');      $msg='Переключаю полноэкранный режим' }
  'bookmarks'{ $wsh.SendKeys('^+o');         $msg='Открываю закладки' }
}
Write-Output "$msg."
