"""A reviewable offline verification entry point, never connects to a broker."""

import subprocess
import sys

COMMANDS = [
    ["uv", "sync", "--frozen"],
    ["uv", "run", "ruff", "check", "src", "tests"],
    ["uv", "run", "pytest", "-q"],
    ["uv", "run", "ashare", "demo", "--scenario", "complete", "--root", "runtime/delivery-check"],
    [
        "uv",
        "run",
        "ashare",
        "data",
        "probe",
        "--provider",
        "tushare",
        "--root",
        "runtime/delivery-check",
    ],
]
if __name__ == "__main__":
    for command in COMMANDS:
        result = subprocess.run(command, check=False)
        if result.returncode:
            sys.exit(result.returncode)
