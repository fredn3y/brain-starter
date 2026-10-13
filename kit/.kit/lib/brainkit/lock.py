"""One catch-up (or update) at a time: a non-blocking `flock` on `.kit/run.lock`.

The operating system drops the lock when the process exits, so a crashed run never leaves a
stale lock behind.
"""

from __future__ import annotations

import errno
import fcntl
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

_BUSY = {errno.EAGAIN, errno.EWOULDBLOCK, errno.EACCES}


@contextmanager
def try_lock(path) -> Iterator[bool]:
    """Yield True while holding the lock, or False at once if another process holds it."""
    lock_path = Path(path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o644)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno not in _BUSY:
                raise
            held = False
        else:
            held = True
            _write_pid(fd)
        try:
            yield held
        finally:
            if held:
                fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def _write_pid(fd: int) -> None:
    """Note who holds the lock (for diagnostics only; the flock is what counts)."""
    try:
        os.ftruncate(fd, 0)
        os.write(fd, f"{os.getpid()}\n".encode("ascii"))
    except OSError:
        pass
