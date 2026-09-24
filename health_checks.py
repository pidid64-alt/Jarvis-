#!/usr/bin/env python3
"""Read-only structured metrics for autonomy. No cloud or third-party packages.

Метрики собираются там, где они есть: Linux — /proc и sysfs, Windows —
ctypes/SystemPowerStatus и shutil. Отсутствующий датчик — не авария:
проверка вернёт пустые metrics, и пороговое правило ничего не сообщит.
"""
import argparse
import json
import os
import shutil
import subprocess
from pathlib import Path


def _disk_space() -> dict:
    roots = [Path("C:/")] if os.name == "nt" else [Path("/")]
    roots.append(Path.home())
    disks = []
    seen = set()
    for path in roots:
        try:
            device = path.stat().st_dev
            if device in seen:
                continue
            seen.add(device)
            usage = shutil.disk_usage(path)
        except OSError:
            continue
        free = usage.free / usage.total * 100
        disks.append((str(path), free))
    if not disks:
        return {"metrics": {}, "text": "Диски недоступны."}
    return {"metrics": {"free_percent": min(v for _, v in disks)},
            "text": " ".join(f"На диске {p} свободно {v:.1f} процентов."
                             for p, v in disks)}


def _memory_usage_posix() -> dict:
    info = {}
    for line in Path("/proc/meminfo").read_text().splitlines():
        name, value = line.split(":", 1)
        info[name] = int(value.split()[0])
    used = (1 - info["MemAvailable"] / info["MemTotal"]) * 100
    return {"metrics": {"used_percent": used},
            "text": f"Используется {used:.1f} процентов оперативной памяти."}


def _memory_usage_windows() -> dict:
    import ctypes

    class MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_ulong),
            ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    stat = MEMORYSTATUSEX()
    stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
        raise OSError("GlobalMemoryStatusEx failed")
    used = float(stat.dwMemoryLoad)
    return {"metrics": {"used_percent": used},
            "text": f"Используется {used:.1f} процентов оперативной памяти."}


def _battery_posix() -> dict:
    batteries = []
    for path in Path("/sys/class/power_supply").glob("*"):
        if (path / "type").read_text().strip() != "Battery":
            continue
        status = (path / "status").read_text().strip()
        capacity = float((path / "capacity").read_text())
        batteries.append((capacity, status == "Discharging"))
    # Alert on the least-charged discharging battery (multi-battery laptops).
    discharging = [cap for cap, active in batteries if active]
    if not batteries:
        return {"metrics": {}, "text": "Батарея не найдена."}
    cap = min(discharging) if discharging else min(cap for cap, _ in batteries)
    return {"metrics": {"charge_percent": cap, "on_battery": bool(discharging)},
            "text": f"Заряд батареи {cap:.0f} процентов, " +
                    ("разряжается." if discharging else "питание не от батареи.")}


def _battery_windows() -> dict:
    import ctypes

    class SYSTEM_POWER_STATUS(ctypes.Structure):
        _fields_ = [
            ("ACLineStatus", ctypes.c_ubyte),
            ("BatteryFlag", ctypes.c_ubyte),
            ("BatteryLifePercent", ctypes.c_ubyte),
            ("SystemStatusFlag", ctypes.c_ubyte),
            ("BatteryLifeTime", ctypes.c_ulong),
            ("BatteryFullLifeTime", ctypes.c_ulong),
        ]

    status = SYSTEM_POWER_STATUS()
    if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(status)):
        raise OSError("GetSystemPowerStatus failed")
    if status.BatteryFlag == 128 or status.BatteryLifePercent == 255:
        return {"metrics": {}, "text": "Батарея не найдена."}
    cap = float(status.BatteryLifePercent)
    on_battery = status.ACLineStatus == 0
    return {"metrics": {"charge_percent": cap, "on_battery": on_battery},
            "text": f"Заряд батареи {cap:.0f} процентов, " +
                    ("разряжается." if on_battery else "питание не от батареи.")}


def _cpu_temp_posix() -> dict:
    result = subprocess.run(["sensors", "-j"], capture_output=True, text=True,
                            check=True, timeout=5)
    data = json.loads(result.stdout)
    values = []
    for chip, groups in data.items():
        if not chip.startswith(("coretemp-", "k10temp-", "zenpower-", "cpu_thermal-")):
            continue
        for group in groups.values():
            if isinstance(group, dict):
                values.extend(float(v) for k, v in group.items()
                              if k.startswith("temp") and k.endswith("_input"))
    if not values:
        return {"metrics": {}, "text": "Датчик температуры CPU недоступен."}
    temp = max(values)
    return {"metrics": {"temperature": temp},
            "text": f"Температура процессора {temp:.1f} градусов."}


def _cpu_temp_windows() -> dict:
    # Стандартный API температуры ядра на Windows не даёт. Без сторонних
    # драйверов (LibreHardwareMonitor и т.п.) честный ответ — «недоступен»;
    # пороговое правило при пустых metrics молчит, ложных тревог не будет.
    return {"metrics": {}, "text": "Датчик температуры CPU недоступен."}


def collect(check: str) -> dict:
    windows = os.name == "nt"
    if check == "disk_space":
        return _disk_space()
    if check == "memory_usage":
        return _memory_usage_windows() if windows else _memory_usage_posix()
    if check == "battery_status":
        return _battery_windows() if windows else _battery_posix()
    if check == "cpu_temp":
        return _cpu_temp_windows() if windows else _cpu_temp_posix()
    raise ValueError(f"Unknown check: {check}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("check", choices=["disk_space", "memory_usage", "battery_status", "cpu_temp"])
    args = parser.parse_args()
    try:
        output = collect(args.check)
    except (OSError, ValueError, KeyError, ZeroDivisionError, subprocess.SubprocessError) as exc:
        output = {"metrics": {}, "text": "Данные проверки недоступны.", "error": str(exc)}
    print(json.dumps(output, ensure_ascii=False))


if __name__ == "__main__":
    main()
