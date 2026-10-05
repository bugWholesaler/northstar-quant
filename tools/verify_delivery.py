"""Offline verification in a runtime isolated by source and dependency identity."""

import subprocess
import sys
from pathlib import Path

from ashare.domain.models import stable_id
from ashare.research.runner import code_identity

identity = code_identity()
verification_id = stable_id({"source": identity["source_hash"], "lock": identity["lock_hash"]})
verification_root = Path("runtime") / ("verification-" + verification_id)
COMMANDS = [
    ["uv", "sync", "--frozen"],
    ["uv", "run", "ruff", "check", "src", "tests"],
    ["uv", "run", "pytest", "-q"],
    ["uv", "run", "ashare", "demo", "--scenario", "complete", "--root", str(verification_root)],
    [
        "uv",
        "run",
        "ashare",
        "data",
        "probe",
        "--provider",
        "tushare",
        "--root",
        str(verification_root),
    ],
]
if __name__ == "__main__":
    print("Verification runtime: " + str(verification_root), flush=True)
    for command in COMMANDS:
        result = subprocess.run(command, check=False)
        if result.returncode:
            sys.exit(result.returncode)
