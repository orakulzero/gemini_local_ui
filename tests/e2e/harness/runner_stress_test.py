import subprocess
import sys
import time
import os
import psutil

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))

def run_cmd(args):
    start = time.perf_counter()
    proc = subprocess.run(
        [sys.executable] + args,
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
    )
    elapsed = time.perf_counter() - start
    return proc.returncode, proc.stdout, proc.stderr, elapsed

def main():
    print("=" * 80)
    print("STARTING E2E RUNNER ADVERSARIAL STRESS TEST")
    print("=" * 80)

    # Test 1: Rapid 3x execution of Tier 3 (Cross-Feature Combinations)
    print("\n[STRESS 1] Running Tier 3 back-to-back (3 iterations)...")
    for i in range(1, 4):
        code, out, err, elapsed = run_cmd(["tests/e2e/runner.py", "--tier", "3"])
        passed = "60/60 passed" in out
        print(f"  Iteration {i}: exit_code={code}, passed_marker={passed}, elapsed={elapsed:.2f}s")
        if code != 0 or not passed:
            print(f"FAILED on iteration {i}:\nSTDOUT:\n{out}\nSTDERR:\n{err}")
            sys.exit(1)

    # Test 2: Rapid 3x execution of Tier 4 (Workloads)
    print("\n[STRESS 2] Running Tier 4 back-to-back (3 iterations)...")
    for i in range(1, 4):
        code, out, err, elapsed = run_cmd(["tests/e2e/runner.py", "--tier", "4"])
        passed = "30/30 passed" in out
        print(f"  Iteration {i}: exit_code={code}, passed_marker={passed}, elapsed={elapsed:.2f}s")
        if code != 0 or not passed:
            print(f"FAILED on iteration {i}:\nSTDOUT:\n{out}\nSTDERR:\n{err}")
            sys.exit(1)

    # Test 3: Back-to-back execution of full Master Runner (620 tests)
    print("\n[STRESS 3] Running Full Master Runner back-to-back (2 full iterations)...")
    for i in range(1, 3):
        code, out, err, elapsed = run_cmd(["tests/e2e/runner.py"])
        passed = "620/620 passed" in out
        verified = "[VERIFIED] Test count (620) satisfies >= 600 tests requirement." in out
        print(f"  Full Run {i}: exit_code={code}, 620_passed={passed}, verified_req={verified}, elapsed={elapsed:.2f}s")
        if code != 0 or not passed or not verified:
            print(f"FAILED on full run {i}:\nSTDOUT:\n{out}\nSTDERR:\n{err}")
            sys.exit(1)

    print("\n" + "=" * 80)
    print("ALL ADVERSARIAL STRESS RUNS COMPLETED SUCCESSFULLY WITH EXIT CODE 0")
    print("=" * 80)

if __name__ == "__main__":
    main()
