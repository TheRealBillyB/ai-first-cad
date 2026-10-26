"""The parent stores only the tail of a script's output."""

import subprocess
import sys

from aicad.sandbox import _SCRIPT_OUTPUT_CAP, _wait_script


def test_piped_output_keeps_the_tail_only():
    proc = subprocess.Popen(
        [
            sys.executable,
            "-c",
            (
                "import sys\n"
                "sys.stdout.buffer.write(b'A' * 1500000 + b'END')\n"
                "sys.stdout.buffer.flush()\n"
                "sys.stderr.buffer.write(b'B' * 1500000 + b'ERR')\n"
                "sys.stderr.buffer.flush()\n"
            ),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    out, err = _wait_script(proc, 30, None)
    assert len(out) == _SCRIPT_OUTPUT_CAP
    assert len(err) == _SCRIPT_OUTPUT_CAP
    assert out.endswith(b"END")
    assert err.endswith(b"ERR")
    assert len(out) < 1_500_000
    assert len(err) < 1_500_000


def test_an_output_file_keeps_the_tail_only(tmp_path):
    from aicad.sandbox_windows import _read_text

    path = tmp_path / "stdout.txt"
    path.write_bytes(b"Z" * 1_500_000 + b"TAIL")
    text = _read_text(path)
    assert text.endswith("TAIL")
    assert len(text.encode("utf-8")) == _SCRIPT_OUTPUT_CAP
    assert len(text) < 1_500_000
