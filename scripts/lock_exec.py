#!/usr/bin/env python3
"""Acquire an exclusive non-blocking file lock, then exec the given command.

Usage: lock_exec.py <lock-file> <command> [args...]

Used by run.sh as a portable flock(1) replacement (macOS doesn't ship
flock). The lock is held for the lifetime of the exec'd process because
os.execvp() replaces this process's image in place, and the lock fd is
explicitly marked inheritable (Python 3 sets O_CLOEXEC on new fds by
default per PEP 446 — without os.set_inheritable() the fd, and the flock
with it, would be closed/released right at the exec() call instead of
surviving for the run's duration). The flock is released automatically
when the pipeline run exits, whether it succeeds, fails, or is killed.

Exit code 1 (no exec) means the lock is already held by another run.
"""
from __future__ import annotations

import fcntl
import os
import sys


def main() -> None:
    if len(sys.argv) < 3:
        print("usage: lock_exec.py <lock-file> <command> [args...]", file=sys.stderr)
        sys.exit(2)

    lock_path = sys.argv[1]
    cmd = sys.argv[2:]

    lock_file = open(lock_path, "a")
    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print(
            f"[lock_exec] ERROR: lock already held: {lock_path} "
            f"(another pipeline run is in progress for this issue)",
            file=sys.stderr,
        )
        sys.exit(1)

    # Without this, Python's default O_CLOEXEC on the fd would drop the lock
    # the instant execvp() replaces this process image.
    os.set_inheritable(lock_file.fileno(), True)

    print(f"[lock_exec] acquired lock: {lock_path}", file=sys.stderr)
    os.execvp(cmd[0], cmd)


if __name__ == "__main__":
    main()
