import unittest
import os
import sys

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

tiers = {
    1: os.path.join(PROJECT_ROOT, "tests", "e2e", "tier1_features"),
    2: os.path.join(PROJECT_ROOT, "tests", "e2e", "tier2_boundaries"),
    3: os.path.join(PROJECT_ROOT, "tests", "e2e", "tier3_combinations"),
    4: os.path.join(PROJECT_ROOT, "tests", "e2e", "tier4_workloads")
}

loader = unittest.TestLoader()
grand_total = 0
for tier, path in tiers.items():
    suite = loader.discover(path, pattern="test_*.py")
    count = suite.countTestCases()
    grand_total += count
    files = [f for f in os.listdir(path) if f.startswith("test_") and f.endswith(".py")]
    print(f"Tier {tier}: {count} tests across {len(files)} files: {files}")

print(f"Grand Total Discovered: {grand_total}")
