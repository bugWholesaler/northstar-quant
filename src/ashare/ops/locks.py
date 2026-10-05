import fcntl
from contextlib import contextmanager

from ashare.domain.errors import AshareError


@contextmanager
def account_lock(root, account):
    path = root / "locks" / (account + ".lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise AshareError("LOCK_CONFLICT", "another account writer is running") from None
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)
