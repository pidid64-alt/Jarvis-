$ErrorActionPreference='SilentlyContinue'
[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false)
Add-Type @'
using System;
using System.Runtime.InteropServices;
public class IdleTime {
  [StructLayout(LayoutKind.Sequential)]
  public struct LASTINPUTINFO { public uint cbSize; public uint dwTime; }
  [DllImport("user32.dll")] public static extern bool GetLastInputInfo(ref LASTINPUTINFO plii);
}
'@
$info = New-Object IdleTime+LASTINPUTINFO
$info.cbSize = [uint32][System.Runtime.InteropServices.Marshal]::SizeOf($info)
[IdleTime]::GetLastInputInfo([ref]$info) | Out-Null
$idleMs = ([Environment]::TickCount - $info.dwTime)
$idle = New-TimeSpan -Milliseconds $idleMs
$last = (Get-Date) - $idle
Write-Output "Сессия начата в $($last.ToString('HH:mm')) (простой $($idle.Hours) ч. $($idle.Minutes) мин.)."
