"""
Burst — 10 Repeat Test Suite Runs Under Continuous Multi-Core CPU Load.

Spawns 4 background CPU stress processes (spinning hashlib SHA256 loops)
and executes the full test suite 10 times consecutively under continuous load.
Reports per-run status, durations, pass counts, min, median, max, and spread.
"""
from __future__ import annotations

import multiprocessing
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path


def _cpu_burner(stop_event):
    data = b"BURST_CPU_STRESS_TEST_" * 1024
    while not stop_event.is_set():
        import hashlib
        for _ in range(500):
            hashlib.sha256(data).digest()


def run_pytest_once(run_id: int) -> dict:
    t0 = time.perf_counter()
    res = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-rfE"],
        capture_output=True,
        text=True,
        timeout=240,
    )
    elapsed = time.perf_counter() - t0
    passed = res.returncode == 0
    lines = res.stdout.strip().splitlines()
    summary_line = lines[-1] if lines else ""
    failed_tests = [l for l in lines if "FAILED" in l or "ERROR" in l]
    if not passed:
        Path(f"test_failure_run_{run_id}.log").write_text(res.stdout + "\n" + res.stderr, encoding="utf-8")
    return {
        "run_id": run_id,
        "elapsed": elapsed,
        "passed": passed,
        "summary": summary_line,
        "failed_tests": failed_tests,
    }


def main():
    print("=" * 82)
    print("BURST: 10 REPEAT TEST SUITE RUNS UNDER CONTINUOUS MULTI-CORE CPU LOAD")
    print("=" * 82)

    num_burners = min(4, max(2, multiprocessing.cpu_count() // 2))
    stop_event = multiprocessing.Event()
    burners = [
        multiprocessing.Process(target=_cpu_burner, args=(stop_event,), daemon=True)
        for _ in range(num_burners)
    ]
    for b in burners:
        b.start()
    print(f"Active CPU stress burners: {num_burners} workers spinning hashlib loops.")

    load_times: list[float] = []
    results = []

    try:
        for i in range(1, 11):
            print(f"Run {i:02d}/10 (CPU Load)...", end="", flush=True)
            r = run_pytest_once(i)
            results.append(r)
            load_times.append(r["elapsed"])
            status_str = "PASS" if r["passed"] else "FAIL"
            print(f" {status_str} in {r['elapsed']:.2f}s | {r['summary']}", flush=True)
            if not r["passed"]:
                for f in r.get("failed_tests", []):
                    print(f"    -> {f}", flush=True)
    finally:
        stop_event.set()
        for b in burners:
            b.join(timeout=2)
            if b.is_alive():
                b.terminate()

    print("\n" + "=" * 82)
    print(f"{'Condition':<20} | {'Runs':>5} | {'Passed':>6} | {'Median':>8} | {'Min':>7} | {'Max':>7} | {'Spread':>8}")
    print("-" * 82)

    passed_count = sum(1 for r in results if r["passed"])
    med = statistics.median(load_times)
    mn = min(load_times)
    mx = max(load_times)
    spr = (mx - mn) / 2

    print(f"{'100% CPU Load':<20} | {10:>5} | {passed_count:>5}/10 | {med:>7.2f}s | {mn:>6.2f}s | {mx:>6.2f}s | ±{spr:>6.2f}s")
    print("=" * 82)

    if passed_count == 10:
        print("RESULT: ALL 10 RUNS PASSED (10/10 PASSES, 100% RELIABILITY UNDER CPU LOAD)")
    else:
        print(f"RESULT: FAILED ({passed_count}/10 passed)")


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
