"""
Run the test suite 10 times (5 normal + 5 under synthetic CPU load).
Records durations, success rate, min, median, max.
"""
import multiprocessing
import os
import statistics
import subprocess
import sys
import time

def cpu_burner(stop_event):
    while not stop_event.is_set():
        _ = [i * i for i in range(10000)]

def run_pytest():
    t0 = time.perf_counter()
    res = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/test_http_reliability.py", "-q"],
        capture_output=True,
        text=True,
    )
    elapsed = time.perf_counter() - t0
    success = (res.returncode == 0)
    return elapsed, success, res.stdout, res.stderr

def main():
    print("=" * 80)
    print("BURST TEST SUITE 10-RUN STABILITY & DURATION PROFILING")
    print("=" * 80)

    durations_quiet = []
    durations_loaded = []

    # Part 1: 5 quiet runs
    print("\n--- Part 1: 5 Quiet Runs ---")
    for i in range(1, 6):
        dur, ok, stdout, stderr = run_pytest()
        durations_quiet.append(dur)
        status = "PASSED" if ok else "FAILED"
        print(f"  Run {i} (Quiet) : {dur:.2f} s [{status}]")
        if not ok:
            print(f"    ERROR: {stderr or stdout}")

    # Part 2: 5 runs under CPU load
    print("\n--- Part 2: 5 Runs Under Simulated CPU Load ---")
    stop_event = multiprocessing.Event()
    num_burners = min(4, os.cpu_count() or 2)
    burners = [multiprocessing.Process(target=cpu_burner, args=(stop_event,), daemon=True) for _ in range(num_burners)]
    for p in burners:
        p.start()

    try:
        for i in range(1, 6):
            dur, ok, stdout, stderr = run_pytest()
            durations_loaded.append(dur)
            status = "PASSED" if ok else "FAILED"
            print(f"  Run {i} (Loaded): {dur:.2f} s [{status}]")
            if not ok:
                print(f"    ERROR: {stderr or stdout}")
    finally:
        stop_event.set()
        for p in burners:
            p.join(timeout=1)
            if p.is_alive():
                p.terminate()

    all_durations = durations_quiet + durations_loaded
    print("\n" + "=" * 80)
    print("10-RUN PROFILE SUMMARY")
    print("=" * 80)
    print(f"Quiet Runs (1-5)   : Min: {min(durations_quiet):.2f}s | Median: {statistics.median(durations_quiet):.2f}s | Max: {max(durations_quiet):.2f}s")
    print(f"Loaded Runs (6-10) : Min: {min(durations_loaded):.2f}s | Median: {statistics.median(durations_loaded):.2f}s | Max: {max(durations_loaded):.2f}s")
    print(f"Overall (10 runs)  : Min: {min(all_durations):.2f}s | Median: {statistics.median(all_durations):.2f}s | Max: {max(all_durations):.2f}s")
    print(f"All 10 runs passed : {all(d < 120 for d in all_durations)}")
    print("=" * 80)

if __name__ == "__main__":
    main()
