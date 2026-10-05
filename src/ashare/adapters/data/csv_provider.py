import csv
from pathlib import Path

from ashare.data.snapshot import TABLES, atomic_json, file_hash, publish, validate_tables
from ashare.domain.errors import AshareError
from ashare.domain.models import stable_id


class CSVProvider:
    def ingest(self, root: Path, directory: Path, scope: str, availability: str):
        tables, source_hashes = {}, {}
        for name, model in TABLES.items():
            path = directory / f"{name}.csv"
            if not path.is_file():
                raise AshareError("MISSING_DATASET", f"required CSV missing: {path}")
            source_hashes[path.name] = file_hash(path)
            rows = []
            with path.open(encoding="utf-8-sig", newline="") as stream:
                for index, row in enumerate(csv.DictReader(stream), 2):
                    try:
                        rows.append(model.model_validate({k: v for k, v in row.items() if v != ""}))
                    except ValueError as error:
                        raise AshareError(
                            "CSV_ROW", str(error), file=path.name, line=index
                        ) from error
            tables[name] = rows
        bid = stable_id({"source": source_hashes, "scope": scope, "availability": availability})
        raw = root / "raw" / "csv" / bid
        raw.mkdir(parents=True, exist_ok=True)
        for file in source_hashes:
            source = directory / file
            target = raw / file
            if target.exists() and file_hash(target) != source_hashes[file]:
                raise AshareError("RAW_CONFLICT", "immutable raw data conflict")
            if not target.exists():
                target.write_bytes(source.read_bytes())
        quality = validate_tables(tables)
        atomic_json(raw / "validation.json", quality)
        snapshot = publish(
            root, tables, "csv", scope, availability, {"batch_id": bid, "files": source_hashes}
        )
        atomic_json(
            root / "batches" / f"{bid}.json",
            {"batch_id": bid, "snapshot_id": snapshot.snapshot_id, "quality": quality},
        )
        return {"batch_id": bid, "snapshot_id": snapshot.snapshot_id, "quality": quality}
