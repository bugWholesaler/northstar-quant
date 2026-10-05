from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from datetime import date, datetime
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from ashare.domain.errors import AshareError
from ashare.domain.models import (
    Bar,
    CalendarDay,
    CorporateAction,
    Instrument,
    Snapshot,
    canonical,
    stable_id,
)

TABLES = {
    "instruments": Instrument,
    "calendar": CalendarDay,
    "bars": Bar,
    "actions": CorporateAction,
}


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def atomic_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    with temporary.open("w") as stream:
        stream.write(canonical(value))
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def validate_tables(tables: dict) -> dict:
    instruments = {x.instrument_id: x for x in tables["instruments"]}
    if not instruments or len(instruments) != len(tables["instruments"]):
        raise AshareError("DATA_QUALITY", "empty or duplicate instruments")
    calendar = {(x.exchange, x.trade_date): x.is_open for x in tables["calendar"]}
    if len(calendar) != len(tables["calendar"]) or not calendar:
        raise AshareError("DATA_QUALITY", "empty or duplicate calendar")
    dates = sorted({x.trade_date for x in tables["calendar"]})
    if any((b - a).days != 1 for a, b in zip(dates, dates[1:])):
        raise AshareError("DATA_QUALITY", "calendar must include closed days")
    if any((exchange, day) not in calendar for exchange in ("XSHG", "XSHE") for day in dates):
        raise AshareError("DATA_QUALITY", "both exchange calendars are required")
    bars = {}
    for bar in tables["bars"]:
        key = (bar.instrument_id, bar.trade_date)
        instrument = instruments.get(bar.instrument_id)
        if instrument is None or key in bars:
            raise AshareError("DATA_QUALITY", "unknown instrument or duplicate bar", key=key)
        if not calendar.get((instrument.exchange, bar.trade_date), False):
            raise AshareError("DATA_QUALITY", "bar on closed calendar day", key=key)
        if bar.trade_date < instrument.listed_on or (
            instrument.delisted_on and bar.trade_date >= instrument.delisted_on
        ):
            raise AshareError("DATA_QUALITY", "bar outside listing life", key=key)
        if not bar.suspended and (bar.volume <= 0 or bar.amount <= 0):
            raise AshareError("DATA_QUALITY", "active bar has no turnover", key=key)
        if bar.volume and not bar.suspended:
            average = bar.amount / bar.volume
            if not bar.low * decimal("0.8") <= average <= bar.high * decimal("1.2"):
                raise AshareError("DATA_QUALITY", "volume/amount units inconsistent", key=key)
        bars[key] = bar
    if not bars:
        raise AshareError("DATA_QUALITY", "empty bars")
    first, last = min(x.trade_date for x in bars.values()), max(x.trade_date for x in bars.values())
    missing = []
    for instrument in instruments.values():
        for day in dates:
            if (
                first <= day <= last
                and day >= instrument.listed_on
                and (not instrument.delisted_on or day < instrument.delisted_on)
                and calendar[instrument.exchange, day]
                and (instrument.instrument_id, day) not in bars
            ):
                missing.append((instrument.instrument_id, str(day)))
    if missing:
        raise AshareError(
            "DATA_QUALITY",
            "missing bar/status: suspension requires explicit row",
            missing=missing[:20],
            count=len(missing),
        )
    actions = {x.action_id: x for x in tables["actions"]}
    if len(actions) != len(tables["actions"]):
        raise AshareError("DATA_QUALITY", "duplicate action id")
    for action in actions.values():
        if action.instrument_id not in instruments:
            raise AshareError("DATA_QUALITY", "action for unknown instrument")
    for instrument in instruments:
        sequence = sorted(
            [b for b in bars.values() if b.instrument_id == instrument], key=lambda b: b.trade_date
        )
        for previous, current in zip(sequence, sequence[1:]):
            if current.factor != previous.factor and not any(
                a.instrument_id == instrument and a.ex_date == current.trade_date
                for a in actions.values()
            ):
                raise AshareError(
                    "DATA_QUALITY",
                    "factor change without corporate action",
                    instrument=instrument,
                    day=str(current.trade_date),
                )
    return {
        "status": "READY",
        "rows": {key: len(value) for key, value in tables.items()},
        "missing": 0,
        "units": "CNY/share",
        "full_market_verified": False,
    }


from ashare.domain.models import decimal  # noqa: E402


def publish(
    root: Path,
    tables: dict,
    provider: str,
    scope: str,
    availability: str,
    provenance: dict | None = None,
) -> Snapshot:
    quality = validate_tables(tables)
    if any(b.availability_basis != availability for b in tables["bars"]):
        raise AshareError(
            "AVAILABILITY_MISMATCH", "snapshot label does not match actual bar time basis"
        )
    if availability == "observed" and any(
        a.available_at < a.observed_at for a in tables["actions"]
    ):
        raise AshareError("AVAILABILITY_MISMATCH", "observed action available before observation")
    if scope == "historical_full":
        # CSV completeness is not evidence that every historical security was acquired.
        raise AshareError(
            "SCOPE_NOT_VERIFIED",
            "historical_full requires a separately audited universe manifest; use real_sample",
        )
    normalized = {
        name: sorted([row.model_dump(mode="json") for row in rows], key=canonical)
        for name, rows in tables.items()
    }
    sid = stable_id(
        {
            "tables": normalized,
            "provider": provider,
            "scope": scope,
            "availability": availability,
            "transform": "1.0",
            "provenance": provenance or {},
        }
    )
    destination = root / "snapshots" / sid
    if (destination / "manifest.json").exists():
        return Store(root).load(sid)[0]
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".staging-", dir=destination.parent))
    try:
        files, hashes = {}, {}
        for name, rows in normalized.items():
            file = staging / f"{name}.parquet"
            pq.write_table(
                pa.Table.from_pylist(rows)
                if rows
                else pa.table({"_empty": pa.array([], type=pa.string())}),
                file,
            )
            files[name], hashes[name] = file.name, file_hash(file)
        atomic_json(staging / "provenance.json", provenance or {})
        files["provenance"], hashes["provenance"] = (
            "provenance.json",
            file_hash(staging / "provenance.json"),
        )
        snapshot = Snapshot(
            snapshot_id=sid,
            provider=provider,
            scope=scope,
            files=files,
            hashes=hashes,
            start=min(x.trade_date for x in tables["bars"]),
            end=max(x.trade_date for x in tables["bars"]),
            quality=quality,
            availability=availability,
        )
        atomic_json(staging / "manifest.json", snapshot)
        os.rename(staging, destination)
        return snapshot
    finally:
        if staging.exists():
            shutil.rmtree(staging)


class Store:
    def __init__(self, root: Path):
        self.root = root

    def load(self, sid: str):
        if not sid.isalnum():
            raise AshareError("INVALID_ID", "invalid snapshot id")
        directory = self.root / "snapshots" / sid
        snapshot = Snapshot.model_validate_json((directory / "manifest.json").read_text())
        tables = {}
        for name, file in snapshot.files.items():
            path = directory / file
            if (
                path.parent.resolve() != directory.resolve()
                or file_hash(path) != snapshot.hashes[name]
            ):
                raise AshareError("SNAPSHOT_HASH", "snapshot integrity mismatch", table=name)
            if name in TABLES:
                tables[name] = [
                    TABLES[name].model_validate(row) for row in pq.read_table(path).to_pylist()
                ]
        return snapshot, tables

    def query_bars(self, sid: str, end: date):
        snapshot, _ = self.load(sid)
        with duckdb.connect() as db:
            return db.execute(
                "SELECT * FROM read_parquet(?) WHERE trade_date <= ? ORDER BY instrument_id, trade_date",
                [str(self.root / "snapshots" / sid / snapshot.files["bars"]), str(end)],
            ).fetchall()


class MarketView:
    def __init__(self, snapshot: Snapshot, tables: dict, decision_at: datetime):
        self.snapshot = snapshot
        self.decision_at = decision_at
        self.instruments = {x.instrument_id: x for x in tables["instruments"]}
        self.calendar = tables["calendar"]
        self._bars = {}
        for b in tables["bars"]:
            if b.available_at <= decision_at and b.trade_date <= decision_at.date():
                self._bars.setdefault(b.instrument_id, []).append(b)
        for bars in self._bars.values():
            bars.sort(key=lambda b: b.trade_date)
        self.actions = [a for a in tables["actions"] if a.available_at <= decision_at]

    def bars(self, instrument_id: str, count: int):
        return self._bars.get(instrument_id, [])[-count:]

    def open_days(self, exchange: str = "XSHG"):
        return sorted(x.trade_date for x in self.calendar if x.exchange == exchange and x.is_open)

    def next_days(self, day: date, count: int):
        days = [d for d in self.open_days() if d > day][:count]
        if len(days) < count:
            raise AshareError("CALENDAR_RANGE", "future calendar coverage insufficient")
        return days

    def is_month_end(self, day: date):
        return self.next_days(day, 1)[0].month != day.month

    def complete_window(self, symbol: str, count: int):
        bars = self.bars(symbol, count)
        inst = self.instruments[symbol]
        expected = [d for d in self.open_days(inst.exchange) if d <= self.decision_at.date()][
            -count:
        ]
        return (
            bars
            if len(bars) == count
            and [b.trade_date for b in bars] == expected
            and not any(b.suspended for b in bars)
            else []
        )
