from ashare.domain.errors import AshareError
from ashare.domain.models import Mode
from ashare.ops.daily import run


class PaperExecution:
    capabilities = {"broker_submission": False, "prospective_simulation": True, "daily_proxy": True}

    def step(self, ledger, root, config, day, snapshot_id, now=None):
        if ledger.mode not in (Mode.DEMO, Mode.PAPER):
            raise AshareError("MODE_ISOLATION", "paper execution cannot target manual/research")
        return run(ledger, root, config, day, snapshot_id, now)
