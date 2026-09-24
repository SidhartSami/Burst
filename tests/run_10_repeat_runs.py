"""
Burst Milestone 7 — 10 Repeat Runs Suite (5 Idle + 5 Under CPU Load).

Executes the full test suite 10 times consecutively:
- Runs 1-5: Baseline idle system
- Runs 6-10: Under artificial multi-core CPU load (spinning hashlib SHA256 loops)
Reports per-run status, durations, pass counts, and summary statistics.
"""
from __future__ import annotations

import multiprocessing
import os
import statistics
import subprocess
import sys
import time


def _cpu_burner(stop_event):
    data = b"BURST_CPU_STRESS_TEST_" * 1024
    while not stop_event.is_set():
        import hashlib
        for _ in range(500):
            hashlib.sha256(data).digest()


def run_pytest_once(run_id: int, mode: str) -> dict:
    t0 = time.perf_counter()
    res = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-rfE"],
        capture_output=True,
        text=True,
        timeout=180,
    )
    elapsed = time.perf_counter() - t0
    passed = res.returncode == 0
    lines = res.stdout.strip().splitlines()
    summary_line = lines[-1] if lines else ""
    failed_tests = [l for l in lines if "FAILED" in l or "ERROR" in l]
    if not passed:
        from pathlib import Path
        Path(f"test_failure_run_{run_id}.log").write_text(res.stdout + "\n" + res.stderr, encoding="utf-8")
    return {
        "run_id": run_id,
        "mode": mode,
        "elapsed": elapsed,
        "passed": passed,
        "summary": summary_line,
        "failed_tests": failed_tests,
    }


def main():
    print("=" * 78)
    print("BURST MILESTONE 7: 10 REPEAT TEST SUITE RUNS (5 Idle + 5 Under CPU Load)")
    print("=" * 78)

    idle_times: list[float] = []
    load_times: list[float] = []
    results = []

    # 1. 5 Idle runs
    print("\n--- PHASE 1: 5 IDLE RUNS ---")
    for i in range(1, 6):
        print(f"Run {i}/10 (Idle)...", end="", flush=True)
        r = run_pytest_once(i, "Idle")
        results.append(r)
        idle_times.append(r["elapsed"])
        status_str = "PASS" if r["passed"] else "FAIL"
        print(f" {status_str} in {r['elapsed']:.2f}s | {r['summary']}")
        if not r["passed"]:
            for f in r.get("failed_tests", []):
                print(f"    -> {f}")

    # 2. 5 Runs under CPU load
    print("\n--- PHASE 2: 5 RUNS UNDER CPU LOAD ---")
    num_burners = min(4, max(2, multiprocessing.cpu_count() // 2))
    stop_event = multiprocessing.Event()
    burners = [
        multiprocessing.Process(target=_cpu_burner, args=(stop_event,), daemon=True)
        for _ in range(num_burners)
    ]
    for b in burners:
        b.start()
    print(f"Started {num_burners} CPU load workers.")

    try:
        for i in range(6, 11):
            print(f"Run {i}/10 (CPU Load)...", end="", flush=True)
            r = run_pytest_once(i, "CPU Load")
            results.append(r)
            load_times.append(r["elapsed"])
            status_str = "PASS" if r["passed"] else "FAIL"
            print(f" {status_str} in {r['elapsed']:.2f}s | {r['summary']}")
            if not r["passed"]:
                for f in r.get("failed_tests", []):
                    print(f"    -> {f}")
    finally:
        stop_event.set()
        for b in burners:
            b.join(timeout=2)
            if b.is_alive():
                b.terminate()

    print("\n" + "=" * 78)
    print(f"{'Condition':<15} | {'Runs':>5} | {'Passed':>6} | {'Median':>8} | {'Min':>7} | {'Max':>7} | {'Spread':>8}")
    print("-" * 78)

    all_passed = all(r["passed"] for r in results)

    med_idle = statistics.median(idle_times)
    mn_idle = min(idle_times)
    mx_idle = max(idle_times)
    spr_idle = (mx_idle - mn_idle) / 2
    pass_idle = sum(1 for r in results[:5] if r["passed"])
    print(f"{'Idle':<15} | {5:>5} | {pass_idle:>6}/5 | {med_idle:>7.2f}s | {mn_idle:>6.2f}s | {mx_idle:>6.2f}s | ±{spr_idle:>6.2f}s")

    med_load = statistics.median(load_times)
    mn_load = min(load_times)
    mx_load = max(load_times)
    spr_load = (mx_load - mn_load) / 2
    pass_load = sum(1 for r in results[5:] if r["passed"])
    print(f"{'CPU Load':<15} | {5:>5} | {pass_load:>6}/5 | {med_load:>7.2f}s | {mn_load:>6.2f}s | {mx_load:>6.2f}s | ±{spr_load:>6.2f}s")

    all_times = idle_times + load_times
    med_all = statistics.median(all_times)
    mn_all = min(all_times)
    mx_all = max(all_times)
    spr_all = (mx_all - mn_all) / 2
    total_passed = sum(1 for r in results if r["passed"])
    print(f"{'Combined':<15} | {10:>5} | {total_passed:>5}/10 | {med_all:>7.2f}s | {mn_all:>6.2f}s | {mx_all:>6.2f}s | ±{spr_all:>6.2f}s")
    print("=" * 78)
    if all_passed:
        print("ALL 10 RUNS PASSED 100% (91/91 tests in every run).")
    else:
        print("SOME RUNS FAILED!")


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
