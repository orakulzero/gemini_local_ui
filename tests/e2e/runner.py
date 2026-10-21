#!/usr/bin/env python3
"""Master E2E Test Suite Runner for Gemini Local UI.

Usage:
    python tests/e2e/runner.py
    python tests/e2e/runner.py --tier 1
    python tests/e2e/runner.py --tier 2
    python tests/e2e/runner.py --tier 3
    python tests/e2e/runner.py --tier 4
"""

import sys
import os
import time
import unittest
import argparse
from typing import Dict, List, Tuple

# Ensure project root is in sys.path
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

TIER_DIRS = {
    1: ("Tier 1 (Feature Coverage)", os.path.join(PROJECT_ROOT, "tests", "e2e", "tier1_features")),
    2: ("Tier 2 (Boundary & Corner Cases)", os.path.join(PROJECT_ROOT, "tests", "e2e", "tier2_boundaries")),
    3: ("Tier 3 (Cross-Feature Combinations)", os.path.join(PROJECT_ROOT, "tests", "e2e", "tier3_combinations")),
    4: ("Tier 4 (Real-World Application Workloads)", os.path.join(PROJECT_ROOT, "tests", "e2e", "tier4_workloads")),
}

class ColoredText:
    GREEN = "\033[92m"
    RED = "\033[91m"
    YELLOW = "\033[93m"
    CYAN = "\033[96m"
    BOLD = "\033[1m"
    RESET = "\033[0m"

    @classmethod
    def ok(cls, text: str) -> str:
        return f"{cls.GREEN}{text}{cls.RESET}"

    @classmethod
    def fail(cls, text: str) -> str:
        return f"{cls.RED}{text}{cls.RESET}"

    @classmethod
    def header(cls, text: str) -> str:
        return f"{cls.CYAN}{cls.BOLD}{text}{cls.RESET}"


def run_tier(tier_num: int, name: str, directory: str, verbosity: int = 1) -> Tuple[int, int, int, float, List[str]]:
    loader = unittest.TestLoader()
    suite = loader.discover(start_dir=directory, pattern="test_*.py")
    runner = unittest.TextTestRunner(verbosity=0, stream=open(os.devnull, "w"))

    start_time = time.perf_counter()
    result = runner.run(suite)
    elapsed = time.perf_counter() - start_time

    total = result.testsRun
    failed = len(result.failures)
    errored = len(result.errors)
    passed = total - (failed + errored)

    error_details = []
    for test, trace in result.failures:
        error_details.append(f"[FAIL] {test.id()}:\n{trace}")
    for test, trace in result.errors:
        error_details.append(f"[ERROR] {test.id()}:\n{trace}")

    return total, passed, (failed + errored), elapsed, error_details


def main():
    parser = argparse.ArgumentParser(description="Master E2E Test Suite Runner for Gemini Local UI")
    parser.add_argument("--tier", type=int, choices=[1, 2, 3, 4], help="Run specific tier only")
    parser.add_argument("--verbose", "-v", action="store_true", help="Print detailed failure traces")
    args = parser.parse_args()

    print("=" * 80)
    print(ColoredText.header(" GEMINI LOCAL UI - SYSTEMATIC 4-TIER E2E TEST RUNNER"))
    print("=" * 80)
    print(f"Project Root: {PROJECT_ROOT}")
    print(f"Timestamp:    {time.strftime('%Y-%m-%d %H:%M:%SZ', time.gmtime())}")
    print("-" * 80)

    selected_tiers = [args.tier] if args.tier else [1, 2, 3, 4]

    tier_results = []
    all_errors = []
    overall_start = time.perf_counter()

    for tier_num in selected_tiers:
        name, directory = TIER_DIRS[tier_num]
        print(f"Running {ColoredText.header(name)} ... ", end="", flush=True)
        total, passed, failures, elapsed, errors = run_tier(tier_num, name, directory)
        status_str = ColoredText.ok("PASSED") if failures == 0 else ColoredText.fail("FAILED")
        print(f"[{status_str}]  {passed}/{total} tests ({elapsed:.2f}s)")
        tier_results.append((tier_num, name, total, passed, failures, elapsed))
        all_errors.extend(errors)

    overall_elapsed = time.perf_counter() - overall_start

    print("-" * 80)
    print(ColoredText.header("SUMMARY OF RESULTS:"))
    print("-" * 80)
    print(f"{'Tier':<8} {'Description':<42} {'Total':<8} {'Passed':<8} {'Failed':<8} {'Time (s)':<8}")
    print("-" * 80)

    grand_total = 0
    grand_passed = 0
    grand_failed = 0

    for tier_num, name, total, passed, failures, elapsed in tier_results:
        grand_total += total
        grand_passed += passed
        grand_failed += failures
        tier_tag = f"Tier {tier_num}"
        print(f"{tier_tag:<8} {name[:40]:<42} {total:<8} {passed:<8} {failures:<8} {elapsed:<8.2f}")

    print("=" * 80)
    status_summary = ColoredText.ok("ALL TESTS PASSED") if grand_failed == 0 else ColoredText.fail("TEST SUITE FAILED")
    print(f"TOTAL: {grand_passed}/{grand_total} passed in {overall_elapsed:.2f}s  -->  {status_summary}")
    print("=" * 80)

    # Verification threshold checks
    if args.tier is None:
        min_required = 600
        if grand_total < min_required:
            print(ColoredText.fail(f"WARNING: Test count ({grand_total}) does not satisfy requirement of >= {min_required} tests!"))
            return 1
        else:
            print(ColoredText.ok(f"[VERIFIED] Test count ({grand_total}) satisfies >= {min_required} tests requirement."))

    if all_errors and (args.verbose or grand_failed > 0):
        print("\n" + "=" * 80)
        print(ColoredText.fail("FAILURE / ERROR DETAILS:"))
        print("=" * 80)
        for err in all_errors:
            print(err)
            print("-" * 80)

    return 0 if grand_failed == 0 else 1


if __name__ == "__main__":
    exit_code = main()
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(exit_code)
