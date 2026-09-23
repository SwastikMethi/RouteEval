"""Keep a CLI tied to its runner's lifetime, including an ungraceful backend exit."""

import argparse
import os
import signal
import subprocess
import sys
import time


def main(argv=None):
    parser = argparse.ArgumentParser(prog="routebench-watchdog")
    parser.add_argument("--parent-pid", type=int)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    parent = args.parent_pid if args.parent_pid is not None else os.getppid()
    if parent <= 1 or not command:
        parser.error("a live parent PID and a CLI command are required")
    # The backend may already have died before this interpreter finished starting.
    if os.getppid() != parent:
        return 125
    environment = dict(os.environ)
    environment.pop("ROUTEBENCH_PARENT_PID", None)
    try:
        child = subprocess.Popen(command, env=environment)
    except OSError as error:
        print(f"RouteBench could not start CLI: {error}", file=sys.stderr)
        return 127
    while True:
        if os.getppid() != parent:
            # We lead the dedicated group created by process.supervise.
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
            os.killpg(os.getpgrp(), signal.SIGTERM)
            time.sleep(0.5)
            os.killpg(os.getpgrp(), signal.SIGKILL)
        returncode = child.poll()
        if returncode is not None:
            # sys.exit(negative_signal) wraps modulo 256; use the usual shell status.
            return 128 - returncode if returncode < 0 else returncode
        time.sleep(0.05)


if __name__ == "__main__":
    sys.exit(main())
