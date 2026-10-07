$ErrorActionPreference='SilentlyContinue'
[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false)
Add-Type @'
using System;
using System.Runtime.InteropServices;
public class PowerStatus {
  [StructLayout(LayoutKind.Sequential)]
  public struct SYSTEM_POWER_STATUS {
    public byte ACLineStatus; public byte BatteryFlag; public byte BatteryLifePercent;
    public byte SystemStatusFlag; public int BatteryLifeTime; public int BatteryFullLifeTime;
  }
  [DllImport("kernel32.dll")] public static extern bool GetSystemPowerStatus(out SYSTEM_POWER_STATUS s);
}
'@
$s=New-Object PowerStatus+SYSTEM_POWER_STATUS
if (-not [PowerStatus]::GetSystemPowerStatus([ref]$s)) { Write-Output "Батарея не найдена."; exit 0 }
if ($s.BatteryFlag -eq 128 -or $s.BatteryLifePercent -eq 255) { Write-Output "Батарея не найдена."; exit 0 }
$cap=[int]$s.BatteryLifePercent
$onBattery = ($s.ACLineStatus -eq 0)
$state = if ($onBattery) { "разряжается" } else { "питание не от батареи" }
Write-Output "Заряд батареи: $cap процентов, $state."
