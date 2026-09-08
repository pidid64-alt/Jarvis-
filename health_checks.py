#!/usr/bin/env python3
"""Read-only structured metrics for autonomy. No cloud or third-party packages."""
import argparse
import json
import shutil
import subprocess
from pathlib import Path


def collect(check: str) -> dict:
    if check == "disk_space":
        disks = []
        seen = set()
        for path in (Path("/"), Path.home()):
            device = path.stat().st_dev
            if device in seen:
                continue
            seen.add(device)
            usage = shutil.disk_usage(path)
            free = usage.free / usage.total * 100
            disks.append((str(path), free))
        return {"metrics": {"free_percent": min(v for _, v in disks)},
                "text": " ".join(f"На диске {p} свободно {v:.1f} процентов." for p, v in disks)}
    if check == "memory_usage":
        info = {}
        for line in Path("/proc/meminfo").read_text().splitlines():
            name, value = line.split(":", 1)
            info[name] = int(value.split()[0])
        used = (1 - info["MemAvailable"] / info["MemTotal"]) * 100
        return {"metrics": {"used_percent": used},
                "text": f"Используется {used:.1f} процентов оперативной памяти."}
    if check == "battery_status":
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
    if check == "cpu_temp":
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
