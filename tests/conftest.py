"""Test hooks shared by the suite."""

from __future__ import annotations

import sys


def pytest_runtest_logreport(report) -> None:
    """Print a failure as soon as it happens.

    GitHub Actions stops the Windows job on the clock. pytest's end-of-run
    summary is still in the buffer then, so the log never names the test.
    """
    if not report.failed or report.when not in {"setup", "call"}:
        return
    text = getattr(report, "longreprtext", None) or str(report.longrepr)
    sys.stderr.write(f"\n=== FAIL {report.nodeid} ({report.when}) ===\n{text}\n")
    sys.stderr.flush()
