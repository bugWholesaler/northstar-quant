from datetime import date
from pathlib import Path

import pytest

from ashare.accounting.ledger import Ledger, new_event
from ashare.config import Config
from ashare.data.snapshot import publish
from ashare.data.synthetic import generate
from ashare.domain.models import Mode, at


@pytest.fixture(scope="session")
def synthetic(tmp_path_factory):
    root = tmp_path_factory.mktemp("synthetic")
    tables = generate(root / "csv")
    return tables


@pytest.fixture
def market(tmp_path, synthetic):
    snap = publish(tmp_path, synthetic, "synthetic", "synthetic_sample", "historical_assumption")
    return tmp_path, snap, synthetic


@pytest.fixture
def config():
    return Config(mode=Mode.DEMO, account_id="demo", scope="synthetic_sample")


@pytest.fixture
def ledger(tmp_path):
    instance = Ledger(tmp_path / "state" / "demo.sqlite", "demo", Mode.DEMO)
    instance.append(
        [
            new_event(
                "demo",
                Mode.DEMO,
                "Deposit",
                date(2025, 4, 30),
                {"amount": "10000"},
                event_time=at(date(2025, 4, 30), 8, 0),
            )
        ]
    )
    with instance.db:
        instance.set_meta("reconciliation", "MATCHED")
    yield instance
    instance.close()


@pytest.fixture
def broker_mapping():
    return Path("configs/brokers/manual_csv.yaml")
