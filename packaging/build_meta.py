"""Setuptools backend that also installs the import-path guard."""

from __future__ import annotations

import base64
import hashlib
import zipfile
from pathlib import Path

import setuptools.build_meta as _orig

_HERE = Path(__file__).resolve().parent
_FILES = ("aicad_install_path.py", "aicad_install_path.pth")


def _record_hash(data: bytes) -> str:
    digest = hashlib.sha256(data).digest()
    return "sha256=" + base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def _inject(wheel: Path) -> None:
    payloads = {name: (_HERE / name).read_bytes() for name in _FILES}
    temporary = wheel.with_suffix(wheel.suffix + ".tmp")
    with zipfile.ZipFile(wheel, "r") as src, zipfile.ZipFile(temporary, "w") as dst:
        record_name = next(name for name in src.namelist() if name.endswith(".dist-info/RECORD"))
        lines = []
        for line in src.read(record_name).decode("utf-8").splitlines():
            if not line:
                continue
            if line.split(",", 1)[0] in payloads:
                continue
            lines.append(line)
        for name, data in payloads.items():
            lines.append(f"{name},{_record_hash(data)},{len(data)}")
        record = ("\n".join(lines) + "\n").encode("utf-8")
        written: set[str] = set()
        for info in src.infolist():
            if info.filename in payloads:
                dst.writestr(info.filename, payloads[info.filename])
                written.add(info.filename)
            elif info.filename == record_name:
                dst.writestr(info, record)
            else:
                dst.writestr(info, src.read(info.filename))
        for name, data in payloads.items():
            if name not in written:
                dst.writestr(name, data)
    temporary.replace(wheel)


def get_requires_for_build_wheel(config_settings=None):
    return _orig.get_requires_for_build_wheel(config_settings)


def get_requires_for_build_sdist(config_settings=None):
    return _orig.get_requires_for_build_sdist(config_settings)


def get_requires_for_build_editable(config_settings=None):
    return _orig.get_requires_for_build_editable(config_settings)


def prepare_metadata_for_build_wheel(metadata_directory, config_settings=None):
    return _orig.prepare_metadata_for_build_wheel(metadata_directory, config_settings)


def prepare_metadata_for_build_editable(metadata_directory, config_settings=None):
    return _orig.prepare_metadata_for_build_editable(metadata_directory, config_settings)


def build_wheel(wheel_directory, config_settings=None, metadata_directory=None):
    name = _orig.build_wheel(wheel_directory, config_settings, metadata_directory)
    _inject(Path(wheel_directory) / name)
    return name


def build_editable(wheel_directory, config_settings=None, metadata_directory=None):
    name = _orig.build_editable(wheel_directory, config_settings, metadata_directory)
    _inject(Path(wheel_directory) / name)
    return name


def build_sdist(sdist_directory, config_settings=None):
    return _orig.build_sdist(sdist_directory, config_settings)
