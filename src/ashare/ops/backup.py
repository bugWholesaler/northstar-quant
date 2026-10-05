import json
import shutil
import sqlite3
from pathlib import Path

from ashare.accounting.ledger import Ledger
from ashare.data.snapshot import atomic_json, file_hash
from ashare.domain.errors import AshareError
from ashare.domain.models import Mode, stable_id


def create(ledger, root: Path):
    ledger.replay()
    sid = stable_id(
        {
            "events": [e.model_dump(mode="json") for e in ledger.events()],
            "metadata": list(ledger.db.execute("SELECT * FROM metadata ORDER BY key")),
            "plans": list(ledger.db.execute("SELECT * FROM plans ORDER BY plan_id")),
            "jobs": list(ledger.db.execute("SELECT * FROM jobs ORDER BY job_id")),
            "imports": list(ledger.db.execute("SELECT * FROM imports ORDER BY batch_id")),
            "broker_snapshots": list(
                ledger.db.execute("SELECT * FROM broker_snapshots ORDER BY batch_id")
            ),
            "daily_inputs": list(ledger.db.execute("SELECT * FROM daily_inputs ORDER BY day")),
            "reconciliations": list(ledger.db.execute("SELECT * FROM reconciliations ORDER BY id")),
        }
    )
    directory = root / "backups" / ledger.account_id / sid
    if (directory / "manifest.json").exists():
        return verify(directory)
    directory.mkdir(parents=True, exist_ok=True)
    destination = sqlite3.connect(directory / "ledger.sqlite")
    try:
        ledger.db.backup(destination)
    finally:
        destination.close()
    # Preserve referenced input artifacts; local only, never push account evidence.
    for name in ("raw", "imports", "plans", "snapshots", "batches", "configurations", "paper"):
        source = root / name
        if source.exists():
            shutil.copytree(source, directory / "artifacts" / name, dirs_exist_ok=True)
    hashes = {
        str(p.relative_to(directory)): file_hash(p)
        for p in directory.rglob("*")
        if p.is_file() and not p.name.endswith(("-wal", "-shm"))
    }
    manifest = {
        "backup_id": sid,
        "account_id": ledger.account_id,
        "mode": ledger.mode.value,
        "files": hashes,
        "event_count": len(ledger.events()),
        "projection_hash": stable_id(ledger.state()),
        "offsite_copy": "NOT_CONFIGURED",
    }
    atomic_json(directory / "manifest.json", manifest)
    return verify(directory)


def verify(directory: Path):
    manifest = json.loads((directory / "manifest.json").read_text())
    for name, expected in manifest["files"].items():
        path = directory / name
        if (
            directory.resolve() not in path.resolve().parents
            or not path.exists()
            or file_hash(path) != expected
        ):
            raise AshareError("BACKUP_HASH", "backup integrity check failed", file=name)
    ledger = Ledger(directory / "ledger.sqlite", manifest["account_id"], Mode(manifest["mode"]))
    try:
        if (
            ledger.db.execute("PRAGMA integrity_check").fetchone()[0] != "ok"
            or stable_id(ledger.replay()) != manifest["projection_hash"]
            or len(ledger.events()) != manifest["event_count"]
        ):
            raise AshareError("BACKUP_REPLAY", "backup replay differs")
    finally:
        ledger.close()
    return {
        "backup_id": manifest["backup_id"],
        "state": "VERIFIED",
        "directory": str(directory),
        "event_count": manifest["event_count"],
        "offsite_copy": manifest["offsite_copy"],
    }


def restore(directory: Path, target_root: Path):
    verify(directory)
    manifest = json.loads((directory / "manifest.json").read_text())
    path = target_root / "state" / f"{manifest['account_id']}.sqlite"
    if path.exists():
        raise AshareError("RESTORE_TARGET_EXISTS", "restore into a fresh runtime directory")
    path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(directory / "ledger.sqlite", path)
    if (directory / "artifacts").exists():
        for item in (directory / "artifacts").iterdir():
            shutil.copytree(item, target_root / item.name)
    return {"state": "RESTORED", "root": str(target_root), "account_id": manifest["account_id"]}
