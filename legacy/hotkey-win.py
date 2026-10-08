#!/usr/bin/env python3
"""
hotkey-win.py
-------------
Глобальный хоткей Win+J для Windows без сторонних библиотек и без
прав администратора: классическое Win32 RegisterHotKey + цикл сообщений.

При нажатии пишет trigger-файл демона jarvis.py (platform_support), тот
подхватывает его и запускает запись → STT → команду. Это полный аналог
jarvis-trigger.sh/XFCE-хоткея на Linux.

Запуск: pythonw hotkey-win.py (без консоли) — install-win.ps1 регистрирует
его как Scheduled Task на вход в систему.
"""

import ctypes
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import platform_support as plat  # noqa: E402

# Win32 constants
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000
VK_J = 0x4A  # J
WM_HOTKEY = 0x0312

HOTKEY_ID = 0x4A56  # «JV», произвольный id


def main() -> int:
    if not plat.IS_WINDOWS:
        print("hotkey-win.py работает только на Windows; "
              "на Linux используйте jarvis-trigger.sh.")
        return 1

    import ctypes.wintypes

    user32 = ctypes.windll.user32
    if not user32.RegisterHotKey(None, HOTKEY_ID, MOD_WIN | MOD_NOREPEAT, VK_J):
        print("Не удалось зарегистрировать Win+J (возможно, уже занято).")
        return 1

    print("Win+J зарегистрирован. Ctrl+C — выход.")
    msg = ctypes.wintypes.MSG()
    try:
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
            if msg.message == WM_HOTKEY:
                plat.fire_trigger()
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
    except KeyboardInterrupt:
        pass
    finally:
        user32.UnregisterHotKey(None, HOTKEY_ID)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
