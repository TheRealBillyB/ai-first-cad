"""A script job that cannot start says why, without a path.

"The script job could not be read" used to cover a missing or unreadable
job.json and also aicad failing to load in the container, which was the real
problem on computers with Claude Desktop.
"""

import errno
import json

import pytest

from aicad import sandbox_run

SECRET_PATH = r"C:\Users\someone\project\.aicad\jobs\abc\job.json"


def _os_error(cls, number, winerror=None):
    error = cls(number, "reason text", SECRET_PATH)
    if winerror is not None:
        error.winerror = winerror
    return error


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (_os_error(FileNotFoundError, errno.ENOENT), "job.json is missing (FileNotFoundError, errno 2"),
        (_os_error(PermissionError, errno.EACCES, 5), "access to job.json was denied (PermissionError, errno 13, winerror 5)"),
        (UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte"), "job.json is not UTF-8 (UnicodeDecodeError)"),
        (json.JSONDecodeError("Expecting value", "{x", 1), "job.json is not valid JSON (line 1, column 2)"),
        (None, "job.json is not a JSON object"),
    ],
)
def test_a_job_read_problem_names_the_case_and_no_path(exc, expected):
    message = sandbox_run._job_read_problem(exc, 1000)
    assert expected in message
    assert "someone" not in message and "C:\\" not in message


def test_aicad_failing_to_start_is_not_called_a_job_read():
    exc = OSError(errno.ENOTSUP, "A private folder here would also be open to S-1-1-0.", SECRET_PATH)
    message = sandbox_run._start_problem(exc)
    assert message.startswith(f"aicad could not start in the script container (OSError, errno {errno.ENOTSUP}")
    assert "would also be open to S-1-1-0" in message
    assert "job.json" not in message
    assert "someone" not in message
