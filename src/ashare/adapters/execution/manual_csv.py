from ashare.execution.importer import apply, preview
from ashare.execution.plans import export


class ManualCSVExecution:
    capabilities = {"broker_submission": False, "file_export": True, "event_import": True}

    def publish(self, ledger, root, plan_id, now=None):
        return export(ledger, root, plan_id, now=now)

    def preview_events(self, ledger, root, mapping, file, kind):
        return preview(ledger, root, mapping, file, kind)

    def import_events(self, ledger, root, batch_id):
        return apply(ledger, root, batch_id)
