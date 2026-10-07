#!/usr/bin/env python3
"""Замер памяти Jarvis — одинаково на Linux и Windows, без внешних зависимостей.

Зачем отдельный инструмент: «сколько ест окно» нужно мерить до и после правок
стиля и одинаково на обеих системах. Модуль ``resource`` есть только на Linux,
``psutil`` — лишняя зависимость, поэтому память читается напрямую: на Linux из
``/proc/<pid>/status``, на Windows через ``GetProcessMemoryInfo`` (ctypes).

Каждый сценарий запускается **отдельным процессом**, поэтому замеры не мешают
друг другу. Печатается две цифры: сколько памяти процесс держит сейчас (RSS) и
пиковое значение за время работы.

Запуск::

    python tools/measure_memory.py                # все сценарии
    python tools/measure_memory.py --only idle    # один сценарий
    python tools/measure_memory.py --repeat 3     # лучший из трёх прогонов
    python tools/measure_memory.py --json         # машинный вывод
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# ------------------------------------------------------------------- измерение


def _windows_memory(pid: int) -> tuple[float, float]:
    import ctypes
    from ctypes import wintypes

    class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    process = ctypes.windll.kernel32.OpenProcess(0x1000 | 0x0400, False, pid)  # QUERY_LIMITED|QUERY_INFORMATION
    if not process:
        raise OSError("не удалось открыть процесс")
    try:
        counters = PROCESS_MEMORY_COUNTERS()
        counters.cb = ctypes.sizeof(counters)
        if not ctypes.windll.psapi.GetProcessMemoryInfo(process, ctypes.byref(counters), counters.cb):
            raise OSError("GetProcessMemoryInfo не сработал")
        return counters.WorkingSetSize / 1024 / 1024, counters.PeakWorkingSetSize / 1024 / 1024
    finally:
        ctypes.windll.kernel32.CloseHandle(process)


def process_memory(pid: int) -> tuple[float, float]:
    """(текущая, пиковая) память процесса в мегабайтах."""
    if os.name == "nt":
        return _windows_memory(pid)
    try:
        with open(f"/proc/{pid}/status", encoding="utf-8") as handle:
            current = peak = None
            for line in handle:
                if line.startswith("VmRSS:"):
                    current = int(line.split()[1]) / 1024
                elif line.startswith("VmHWM:"):
                    peak = int(line.split()[1]) / 1024
        if current is None:
            raise OSError("нет VmRSS")
        return current, peak or current
    except OSError:
        import resource

        peak = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss / 1024
        return peak, peak


def threads_of(pid: int) -> int:
    if os.name == "nt":
        return 0
    try:
        with open(f"/proc/{pid}/status", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("Threads:"):
                    return int(line.split()[1])
    except OSError:
        pass
    return 0


# ------------------------------------------------------------------- сценарии

#: код сценария: запускается отдельным процессом
SCENARIOS: dict[str, dict] = {
    "python": {
        "title": "пустой Python (точка отсчёта)",
        "note": "интерпретатор без единого модуля Jarvis — с этой цифры считаем накладные расходы",
        "code": "pass",
    },
    "cli": {
        "title": "одна команда в терминале",
        "note": "jarvis ask «сколько времени» — самое частое использование",
        "code": (
            "from tests.helpers import isolated_home\n"
            "with isolated_home():\n"
            "    from jarvis.interfaces.cli import main\n"
            "    main(['ask', 'сколько времени', '--no-speak'])\n"
        ),
    },
    "daemon": {
        "title": "демон без голоса и автопроверок",
        "note": "ядро с провайдерами и навыками, как в jarvis run --no-voice",
        "code": (
            "from tests.helpers import isolated_home\n"
            "with isolated_home():\n"
            "    from jarvis.core.assistant import create_assistant\n"
            "    assistant = create_assistant()\n"
            "    print('MEM_READY: демон поднят')"
        ),
    },
    "core_api": {
        "title": "ядро + локальный API",
        "note": "то, что окно поднимает у себя в процессе",
        "code": (
            "from tests.helpers import isolated_home\n"
            "with isolated_home():\n"
            "    from jarvis.core.assistant import create_assistant\n"
            "    from jarvis.interfaces.api import LocalApi\n"
            "    assistant = create_assistant()\n"
            "    api = LocalApi(assistant)\n"
            "    api.start()\n"
            "    print('MEM_READY: API на', api.port)"
        ),
    },
    "gui_module": {
        "title": "модуль окна импортирован (без Tkinter)",
        "note": "накладные расходы кода окна, если окно не открывать",
        "code": (
            "import jarvis.interfaces.gui  # noqa: F401\n"
            "import jarvis.interfaces.tray  # noqa: F401\n"
            "print('MEM_READY: модуль окна загружен')"
        ),
    },
    "gui_window": {
        "title": "окно открыто и работает",
        "note": "только там, где есть Tkinter; окно открывается и закрывается само",
        "requires_tk": True,
        "code": (
            "import threading, time\n"
            "from tests.helpers import isolated_home\n"
            "with isolated_home():\n"
            "    from jarvis.interfaces.gui import GuiApp, connect_or_start\n"
            "    client, embedded = connect_or_start()\n"
            "    app = GuiApp(client, tray=False, embedded=embedded)\n"
            "    print('MEM_READY: окно открыто')\n"
            "    threading.Timer(6.0, app.root.destroy).start()\n"
            "    app.root.mainloop()\n"
            "    if embedded:\n"
            "        embedded[1].stop(); embedded[0].shutdown()"
        ),
    },
}


def build_script(scenario: dict) -> str:
    """Собрать текст скрипта: сценарий и «удержание» процесса на время замера."""
    return "\n".join([
        "import sys, time",
        f"sys.path.insert(0, {str(PROJECT_ROOT)!r})",
        "",
        scenario["code"],
        "",
        "# держим процесс живым, чтобы замер успел снять пик памяти",
        "time.sleep(1.5)",
        "",
    ])


def run_scenario(name: str) -> dict:
    """Запустить сценарий в отдельном процессе и замерить его память."""
    scenario = SCENARIOS[name]
    started = time.time()
    process = subprocess.Popen(
        [sys.executable, "-c", build_script(scenario)],
        cwd=str(PROJECT_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
    )
    peak = current = 0.0
    threads = 0
    while process.poll() is None:
        try:
            now, high = process_memory(process.pid)
        except OSError:
            break
        current = max(current, now)
        peak = max(peak, high)
        threads = max(threads, threads_of(process.pid))
        time.sleep(0.05)
    output = process.stdout.read() if process.stdout else ""
    process.wait()
    return {
        "name": name,
        "title": scenario["title"],
        "note": scenario["note"],
        "rss_mb": round(current, 1),
        "peak_mb": round(peak, 1),
        "threads": threads,
        "seconds": round(time.time() - started, 2),
        "exit": process.returncode,
        "error": "" if process.returncode == 0 else output.strip().splitlines()[-1][:220],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Замерить память Jarvis")
    parser.add_argument("--only", action="append", default=[], choices=list(SCENARIOS),
                        help="сценарий (можно несколько раз)")
    parser.add_argument("--repeat", type=int, default=1, help="сколько раз повторить (берётся лучшее)")
    parser.add_argument("--json", action="store_true", help="вывести JSON")
    args = parser.parse_args(argv)

    have_tk = True
    try:
        import tkinter  # noqa: F401
    except Exception:  # noqa: BLE001
        have_tk = False

    names = args.only or list(SCENARIOS)
    results = []
    for name in names:
        scenario = SCENARIOS[name]
        if scenario.get("requires_tk") and not have_tk:
            results.append({
                "name": name, "title": scenario["title"], "note": scenario["note"],
                "rss_mb": None, "peak_mb": None, "threads": 0, "seconds": 0.0, "exit": None,
                "error": "нет Tkinter: сценарий пропущен",
            })
            continue
        runs = [run_scenario(name) for _ in range(max(1, args.repeat))]
        best = min(runs, key=lambda item: item["rss_mb"] or 1e9)
        best["runs"] = len(runs)
        results.append(best)

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return 0

    with_tk = "есть" if have_tk else "нет"
    print(f"Замер памяти Jarvis · Python {sys.version.split()[0]} · Tkinter: {with_tk} · "
          f"{'Windows' if os.name == 'nt' else 'Linux'}")
    print(f"{'сценарий':<38}{'сейчас':>10}{'пик':>10}{'потоки':>8}")
    for item in results:
        if item["rss_mb"] is None:
            print(f"{item['title']:<38}{'—':>10}{'—':>10}{'—':>8}   {item['error']}")
            continue
        print(f"{item['title']:<38}{item['rss_mb']:>8.1f} МБ{item['peak_mb']:>8.1f} МБ{item['threads']:>8}")
    for item in results:
        if item["error"] and item["rss_mb"] is not None:
            print(f"  · {item['title']}: завершился с кодом {item['exit']} — {item['error']}")
    print("\nПояснение: «сейчас» — память в конце работы, «пик» — максимум за процесс.")
    print("Точка отсчёта «пустой Python» показывает накладные расходы интерпретатора.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
