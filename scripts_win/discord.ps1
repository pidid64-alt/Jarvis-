param([Parameter(Mandatory=$true)][string]$Action, [string]$Name = '')
$ErrorActionPreference='SilentlyContinue'
[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false)
Add-Type @'
using System;
using System.Runtime.InteropServices;
public class DFocus {
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
}
'@
$proc = Get-Process -Name 'Discord','DiscordCanary','DiscordPTB' -ErrorAction SilentlyContinue |
        Where-Object MainWindowTitle | Select-Object -First 1
if (-not $proc -and ($Action -eq 'call' -or $Action -eq 'ensure')) {
  Start-Process 'Discord' -ErrorAction SilentlyContinue
  Start-Sleep -Seconds 8
  $proc = Get-Process -Name 'Discord','DiscordCanary','DiscordPTB' -ErrorAction SilentlyContinue |
          Where-Object MainWindowTitle | Select-Object -First 1
}
if (-not $proc) { Write-Output "Discord не запущен и не открылся."; exit 1 }

function Activate-Discord {
  [DFocus]::ShowWindow($proc.MainWindowHandle, 9) | Out-Null
  [DFocus]::SetForegroundWindow($proc.MainWindowHandle) | Out-Null
  Start-Sleep -Milliseconds 400
}
$wsh = New-Object -ComObject WScript.Shell

switch ($Action) {
  'call' {
    if (-not $Name) { Write-Output "Не указан контакт."; exit 1 }
    # Читаем contacts из discord_contacts.json рядом с jarvis
    $contactsPath = Join-Path (Split-Path $PSScriptRoot -Parent) 'discord_contacts.json'
    $username = $Name
    if (Test-Path $contactsPath) {
      $contacts = (Get-Content $contactsPath -Raw | ConvertFrom-Json).contacts
      $hit = $contacts | Where-Object { $_.name -eq $Name } | Select-Object -First 1
      if ($hit) { $username = $hit.username }
    }
    Activate-Discord
    $wsh.SendKeys('^k'); Start-Sleep -Milliseconds 500
    $wsh.SendKeys($username); Start-Sleep -Milliseconds 800
    $wsh.SendKeys('{ENTER}'); Start-Sleep -Milliseconds 1200
    # Звонок в личке: Ctrl+' (тот же хоткей, что и на Linux)
    $wsh.SendKeys("^'")
    Write-Output "Звоню $username в Discord."
  }
  'hangup' {
    Activate-Discord
    $wsh.SendKeys('^{ENTER}')   # завершить/поднять звонок в Discord
    Write-Output "Управляю звонком в Discord."
  }
  'hotkey' {
    # $Name — готовая комбинация в нотации WScript, например ^+m
    Activate-Discord
    $wsh.SendKeys($Name)
    Write-Output "Готово."
  }
}
