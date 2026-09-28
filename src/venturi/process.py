"""Linux tool launcher: resource limit and death signal before exec, no shell."""

import ctypes
import os
import resource
import signal
import subprocess
import sys


def main() -> None:
    parent, memory_mb, *command = sys.argv[1:]

    def stop_group(*_):
        os.killpg(os.getpgrp(), signal.SIGKILL)

    signal.signal(signal.SIGTERM, stop_group)
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(1, signal.SIGTERM, 0, 0, 0) != 0:  # PR_SET_PDEATHSIG
        raise OSError(ctypes.get_errno(), "Cannot set parent-death signal")
    if os.getppid() != int(parent):
        sys.exit(125)
    if int(memory_mb):
        limit = int(memory_mb) * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
    # Keep a tiny supervisor alive so parent death also kills tool descendants.
    result = subprocess.run(command)
    sys.exit(result.returncode if result.returncode >= 0 else 128 - result.returncode)


if __name__ == "__main__":
    main()
