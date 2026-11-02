from __future__ import annotations

import os
from pathlib import Path
from typing import BinaryIO


class RuntimeInstanceAlreadyActive(RuntimeError):
    """Another Sidecar process already owns this user's runtime database."""


class RuntimeInstanceLock:
    """Hold an OS-released exclusive lock for one Sidecar process per data directory."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._handle: BinaryIO | None = None

    def acquire(self) -> RuntimeInstanceLock:
        if self._handle is not None:
            raise RuntimeError("runtime instance lock is already held")
        self._path.parent.mkdir(parents=True, exist_ok=True)
        handle = self._path.open("a+b", buffering=0)
        try:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            handle.close()
            raise RuntimeInstanceAlreadyActive from error
        self._handle = handle
        return self

    def close(self) -> None:
        handle = self._handle
        self._handle = None
        if handle is None:
            return
        try:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            # Closing the descriptor also releases the OS-level lock.
            pass
        finally:
            handle.close()

    def __enter__(self) -> RuntimeInstanceLock:
        return self.acquire()

    def __exit__(self, *_: object) -> None:
        self.close()
