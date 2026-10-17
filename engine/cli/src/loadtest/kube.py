"""Thin wrapper over kubectl/helm subprocess calls. Injectable as a `Runner` for tests."""

import subprocess
from collections.abc import Callable

from loadtest.config import parse_cpu_m, parse_memory_mi

Runner = Callable[[list[str], str | None], str]


class CommandError(Exception):
    def __init__(self, cmd: list[str], returncode: int, stderr: str):
        self.cmd = cmd
        self.returncode = returncode
        self.stderr = stderr
        super().__init__(f"`{' '.join(cmd)}` exited {returncode}: {stderr.strip()}")


def run(cmd: list[str], stdin: str | None = None) -> str:
    """Run `cmd`, feeding `stdin`; return stdout or raise CommandError (incl. stderr)."""
    try:
        proc = subprocess.run(cmd, check=False, input=stdin, capture_output=True, text=True)
    except OSError as exc:  # e.g. kubectl not installed
        raise CommandError(cmd, 127, str(exc)) from exc
    if proc.returncode != 0:
        raise CommandError(cmd, proc.returncode, proc.stderr)
    return proc.stdout


def parse_top(line: str) -> tuple[int, int]:
    """`kubectl top pod --no-headers` line -> (cpu millicores, memory Mi)."""
    parts = line.split()
    if len(parts) < 3:
        raise ValueError(f"unexpected `kubectl top` line: {line!r}")
    return parse_cpu_m(parts[1]), parse_memory_mi(parts[2])
