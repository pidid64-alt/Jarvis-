"""Метрики системы без сторонних пакетов: память, диск, батарея, температура.

Используются навыком «Состояние системы» и автономными проверками, чтобы
не зависеть от конкретных утилит конкретного дистрибутива.
"""

from __future__ import annotations

import ctypes
import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Metrics:
    values: dict[str, float] = field(default_factory=dict)
    text: str = ""
    details: dict[str, str] = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps({"text": self.text, "metrics": self.values, "details": self.details},
                          ensure_ascii=False)

    def get(self, key: str) -> float | None:
        return self.values.get(key)


def _proc_meminfo() -> dict[str, int]:
    data: dict[str, int] = {}
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            name, _, rest = line.partition(":")
            value = rest.strip().split()[0]
            if value.isdigit():
                data[name] = int(value) * 1024
    except OSError:
        pass
    return data


def memory() -> Metrics:
    if os.name != "nt":
        info = _proc_meminfo()
        total = info.get("MemTotal", 0)
        available = info.get("MemAvailable", info.get("MemFree", 0))
        if total:
            used_percent = round((total - available) / total * 100, 1)
            gb = 1024 ** 3
            return Metrics(
                values={"total_gb": round(total / gb, 1), "used_percent": used_percent,
                        "available_gb": round(available / gb, 1)},
                text=f"Память занята на {used_percent}%, свободно {round(available / gb, 1)} ГБ.",
            )
        return Metrics(text="Данные о памяти недоступны.")

    class MemoryStatus(ctypes.Structure):
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

    status = MemoryStatus()
    status.dwLength = ctypes.sizeof(MemoryStatus)
    try:
        ok = ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        ok = 0
    if not ok:
        return Metrics(text="Данные о памяти недоступны.")
    gb = 1024 ** 3
    used_percent = float(status.dwMemoryLoad)
    available_gb = round(status.ullAvailPhys / gb, 1)
    return Metrics(
        values={"total_gb": round(status.ullTotalPhys / gb, 1),
                "used_percent": used_percent, "available_gb": available_gb},
        text=f"Память занята на {used_percent:.0f}%, свободно {available_gb} ГБ.",
    )


def disk_space(path: str | None = None) -> Metrics:
    targets = []
    if os.name == "nt":
        targets = [Path(os.environ.get("SystemDrive", "C:") + "/")]
    else:
        targets = [Path("/"), Path.home()]
    if path:
        targets = [Path(path)]
    seen: set[int] = set()
    best_percent = 100.0
    details: list[str] = []
    for target in targets:
        try:
            stat = target.stat()
            if stat.st_dev in seen:
                continue
            seen.add(stat.st_dev)
            usage = shutil.disk_usage(target)
        except OSError:
            continue
        percent = round(usage.free / usage.total * 100, 1) if usage.total else 0.0
        best_percent = min(best_percent, percent)
        details.append(f"{target}: свободно {percent}%")
    if not details:
        return Metrics(text="Не удалось определить свободное место.")
    return Metrics(values={"free_percent": best_percent}, text="; ".join(details) + ".")


def battery() -> Metrics:
    if os.name == "nt":
        class PowerStatus(ctypes.Structure):
            _fields_ = [
                ("ACLineStatus", ctypes.c_ubyte),
                ("BatteryFlag", ctypes.c_ubyte),
                ("BatteryLifePercent", ctypes.c_ubyte),
                ("Reserved1", ctypes.c_ubyte),
                ("BatteryLifeTime", ctypes.c_ulong),
                ("BatteryFullLifeTime", ctypes.c_ulong),
            ]

        status = PowerStatus()
        try:
            ok = ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(status))  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            ok = 0
        if ok and status.BatteryLifePercent != 255:
            on_battery = status.ACLineStatus == 0
            return Metrics(
                values={"charge_percent": float(status.BatteryLifePercent),
                        "on_battery": 1.0 if on_battery else 0.0},
                text=(f"Батарея {status.BatteryLifePercent}%"
                      + (" (от батареи)." if on_battery else " (от сети).")),
            )
        return Metrics(text="Батарея не обнаружена.")
    for supply in Path("/sys/class/power_supply").glob("BAT*"):
        try:
            charge = (supply / "capacity").read_text().strip()
            online = (supply / "status").read_text().strip().lower()
        except OSError:
            continue
        percent = float(charge)
        on_battery = online == "discharging"
        return Metrics(
            values={"charge_percent": percent, "on_battery": 1.0 if on_battery else 0.0},
            text=f"Батарея {percent:.0f}%" + (" — от батареи." if on_battery else " — от сети."),
        )
    return Metrics(text="Батарея не обнаружена.")


def cpu_temperature() -> Metrics:
    """Температура CPU: sysfs (Linux) или WMI (Windows)."""
    if os.name != "nt":
        readings: list[float] = []
        for zone in Path("/sys/class/thermal").glob("thermal_zone*"):
            try:
                name = (zone / "type").read_text().strip().lower()
                value = float((zone / "temp").read_text().strip()) / 1000.0
            except (OSError, ValueError):
                continue
            if "cpu" in name or "x86" in name or "core" in name or "soc" in name:
                readings.append(value)
        for hwmon in Path("/sys/class/hwmon").glob("hwmon*"):
            try:
                name = (hwmon / "name").read_text().strip().lower()
            except OSError:
                continue
            if name not in {"coretemp", "k10temp", "zenpower", "cpu_thermal"}:
                continue
            for sensor in hwmon.glob("temp*_input"):
                try:
                    readings.append(float(sensor.read_text().strip()) / 1000.0)
                except (OSError, ValueError):
                    continue
        if readings:
            hottest = max(readings)
            return Metrics(values={"temperature": round(hottest, 1)},
                           text=f"Температура процессора {hottest:.0f} градусов.")
        return Metrics(text="Датчик температуры недоступен.")
    try:
        output = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "(Get-CimInstance -Namespace root/wmi -ClassName MSAcpi_ThermalZoneTemperature "
             "-ErrorAction SilentlyContinue).CurrentTemperature"],
            capture_output=True, timeout=20,
        )
        values = [float(part) for part in output.stdout.decode(errors="ignore").split()]
        if not values:
            return Metrics(text="Датчик температуры недоступен.")
        celsius = max(values) / 10.0 - 273.15
        return Metrics(values={"temperature": round(celsius, 1)},
                       text=f"Температура процессора {celsius:.0f} градусов.")
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return Metrics(text="Датчик температуры недоступен.")


def internet_available(host: str = "1.1.1.1") -> bool:
    """Проверка сети без внешних пакетов."""
    if os.name == "nt":
        args = ["ping", "-n", "1", "-w", "1000", host]
    else:
        args = ["ping", "-c", "1", "-W", "1", host]
    try:
        result = subprocess.run(args, capture_output=True, timeout=5)
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def collect(name: str) -> Metrics:
    """Собирает одну метрику по имени (используется автономными проверками)."""
    if name == "memory":
        return memory()
    if name == "disk":
        return disk_space()
    if name == "battery":
        return battery()
    if name == "cpu_temp":
        return cpu_temperature()
    if name == "internet":
        available = internet_available()
        return Metrics(values={"available": 1.0 if available else 0.0},
                       text="Интернет есть." if available else "Интернета нет.")
    return Metrics(text=f"Неизвестная проверка: {name}")
