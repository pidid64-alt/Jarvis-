param([Parameter(Mandatory=$true)][string]$Action)
$ErrorActionPreference='SilentlyContinue'
[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false)
Add-Type @'
using System;
using System.Runtime.InteropServices;
public class MediaKeys {
  [DllImport("user32.dll")] public static extern void keybd_event(byte bVk, byte bScan, uint dwFlags, UIntPtr dwExtraInfo);
  public const byte VK_VOLUME_MUTE=0xAD, VK_VOLUME_DOWN=0xAE, VK_VOLUME_UP=0xAF,
                    VK_MEDIA_NEXT=0xB0, VK_MEDIA_PREV=0xB1, VK_MEDIA_STOP=0xB2,
                    VK_MEDIA_PLAY_PAUSE=0xB3;
  public static void Tap(byte vk){ keybd_event(vk,0,0,UIntPtr.Zero); keybd_event(vk,0,2,UIntPtr.Zero); }
}
'@
switch ($Action) {
  'play_pause' { [MediaKeys]::Tap([MediaKeys]::VK_MEDIA_PLAY_PAUSE) }
  'next'       { [MediaKeys]::Tap([MediaKeys]::VK_MEDIA_NEXT) }
  'prev'       { [MediaKeys]::Tap([MediaKeys]::VK_MEDIA_PREV) }
  'stop'       { [MediaKeys]::Tap([MediaKeys]::VK_MEDIA_STOP) }
  'volume_up'  { [MediaKeys]::Tap([MediaKeys]::VK_VOLUME_UP) }
  'volume_down'{ [MediaKeys]::Tap([MediaKeys]::VK_VOLUME_DOWN) }
  'mute'       { [MediaKeys]::Tap([MediaKeys]::VK_VOLUME_MUTE) }
}
