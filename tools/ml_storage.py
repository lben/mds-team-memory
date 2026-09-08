"""Bound offline asset allocations in one dedicated, locked managed directory."""

import os
import shutil
from contextlib import contextmanager
from pathlib import Path

MAX_BYTES = 16 * 1024**3
RESERVE_BYTES = 2 * 1024**3


def add_budget_arguments(parser, required=True):
    parser.add_argument("--managed-root", type=Path, required=required,
                        help="Dedicated root containing models, wheels, runtime, and transfer staging")
    parser.add_argument("--account", type=Path, action="append", default=[],
                        help="Also count managed assets/runtime outside that root; symlink targets are not followed (repeatable)")
    parser.add_argument("--max-bytes", type=int, default=MAX_BYTES, help="Managed byte ceiling; can only lower the 16 GiB default")
    parser.add_argument("--reserve-bytes", type=int, default=RESERVE_BYTES, help="Filesystem reserve; at least 2 GiB")


def tree_size(path):
    if path.is_symlink():
        # Virtual environments contain interpreter links. Count their own bytes,
        # and require --account for managed targets stored outside the root.
        return path.lstat().st_size
    if path.is_file():
        return path.stat().st_size
    if not path.is_dir():
        raise ValueError(f"Managed storage is not a regular file or directory: {path}")
    total = 0
    with os.scandir(path) as entries:
        for entry in entries:
            total += tree_size(Path(entry.path))
    return total


def publish_generation(temporary, destination):
    # Public model assets must remain readable by the non-root inference account.
    for directory, _, _ in os.walk(temporary):
        os.chmod(directory, 0o755)
    if os.path.lexists(destination):
        raise FileExistsError("Destination appeared while preparing the generation")
    os.rename(temporary, destination)


class Budget:
    def __init__(self, root, accounts=(), max_bytes=MAX_BYTES, reserve_bytes=RESERVE_BYTES):
        if type(max_bytes) is not int or not 0 < max_bytes <= MAX_BYTES:
            raise ValueError("Managed byte ceiling must be positive and at most 16 GiB")
        if type(reserve_bytes) is not int or reserve_bytes < RESERVE_BYTES:
            raise ValueError("Filesystem reserve must be at least 2 GiB")
        self.root = root.resolve()
        self.max_bytes, self.reserve_bytes = max_bytes, reserve_bytes
        paths = sorted({self.root, *(path.resolve() for path in accounts)}, key=lambda path: len(path.parts))
        self.accounts = []
        for path in paths:
            if not any(path.is_relative_to(parent) for parent in self.accounts):
                self.accounts.append(path)

    def contains(self, destination):
        if not destination.resolve().is_relative_to(self.root):
            raise ValueError("Destination must be inside the managed root")

    def check(self, additional=0):
        if sum(tree_size(path) for path in self.accounts) + additional > self.max_bytes:
            raise ValueError("Allocation exceeds the managed storage ceiling")
        if shutil.disk_usage(self.root).free < additional + self.reserve_bytes:
            raise ValueError("Allocation would consume the filesystem free-space reserve")


@contextmanager
def allocation(root, accounts=(), max_bytes=MAX_BYTES, reserve_bytes=RESERVE_BYTES):
    """Serialize managed allocations; kernel locks also release after interruption."""
    budget = Budget(root, accounts, max_bytes, reserve_bytes)
    budget.root.mkdir(parents=True, exist_ok=True)
    lock_path = budget.root / ".ml-storage.lock"
    if lock_path.is_symlink():
        raise ValueError("The storage lock must not be a symlink")
    with lock_path.open("a+b") as lock:
        if lock.tell() == 0:
            lock.write(b"0")
            lock.flush()
        lock.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            budget.check()
            yield budget
        finally:
            if os.name == "nt":
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
