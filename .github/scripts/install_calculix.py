#!/usr/bin/env python3
"""Install the pinned CalculiX solver on the macOS and Windows CI runners.

Linux keeps using the apt package from install.py. This script does not run
on Linux. It downloads the conda-forge files named in .github/calculix.lock.json,
checks each sha256, and links them with a pinned micromamba. ccx stays a
separate program. A runner this lock does not cover stops the job.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import stat
import subprocess
import sys
import tarfile
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LOCK_PATH = ROOT / ".github" / "calculix.lock.json"
HEX64 = set("0123456789abcdef")


class UnsupportedRunner(Exception):
    """This operating system has no pinned CalculiX build in the lock."""


def load_lock(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def validate_lock(lock: dict) -> None:
    """Reject a lock that is not the pinned conda-forge set this job expects."""

    if lock.get("calculix_version") != "2.23":
        raise ValueError("the lock must pin CalculiX 2.23")
    micromamba = lock.get("micromamba")
    if not isinstance(micromamba, dict) or micromamba.get("version") != "2.3.3":
        raise ValueError("the lock must pin micromamba 2.3.3")
    platforms = lock.get("platforms")
    if not isinstance(platforms, dict) or set(platforms) != {"osx-arm64", "win-64"}:
        raise ValueError("the lock must cover osx-arm64 and win-64")
    for key in ("osx-arm64", "win-64"):
        _validate_fetch(micromamba.get(key), f"micromamba {key}")
        spec = platforms[key]
        if not isinstance(spec, dict):
            raise ValueError(f"{key} is not an object")
        path_dirs = spec.get("path")
        if not isinstance(path_dirs, list) or not path_dirs or not all(isinstance(d, str) and d for d in path_dirs):
            raise ValueError(f"{key} needs the directories that hold ccx")
        packages = spec.get("packages")
        if not isinstance(packages, list) or not packages:
            raise ValueError(f"{key} has no packages")
        names = [pkg.get("name") for pkg in packages if isinstance(pkg, dict)]
        if names.count("calculix") != 1:
            raise ValueError(f"{key} must contain one calculix package")
        if any("mkl" in str(pkg.get("filename", "")).lower() for pkg in packages if isinstance(pkg, dict)):
            raise ValueError(f"{key} must not pull MKL")
        blas = [pkg for pkg in packages if isinstance(pkg, dict) and pkg.get("name") == "libblas"]
        if len(blas) != 1 or "openblas" not in str(blas[0].get("build", "")):
            raise ValueError(f"{key} must pin libblas to OpenBLAS")
        for pkg in packages:
            if not isinstance(pkg, dict):
                raise ValueError(f"{key} has a package that is not an object")
            _validate_fetch(pkg, f"{key} {pkg.get('name')}")
            if pkg["name"] == "calculix" and (pkg.get("version") != "2.23" or not pkg.get("build")):
                raise ValueError(f"{key} calculix must be version 2.23 with a build string")
            filename = pkg["filename"]
            if Path(str(pkg["url"])).name != filename:
                raise ValueError(f"{filename} does not match its url")


def _validate_fetch(entry: object, label: str) -> None:
    if not isinstance(entry, dict):
        raise ValueError(f"{label} is missing")
    url = entry.get("url")
    digest = entry.get("sha256")
    size = entry.get("size")
    if not isinstance(url, str) or not url.startswith("https://"):
        raise ValueError(f"{label} url must be https")
    if not isinstance(digest, str) or len(digest) != 64 or any(ch not in HEX64 for ch in digest):
        raise ValueError(f"{label} sha256 must be 64 lowercase hex characters")
    if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
        raise ValueError(f"{label} size must be a positive integer")


def select_platform(system: str, machine: str) -> str | None:
    """The lock key for this runner. Linux returns None. Anything else is an error."""

    machine = machine.lower()
    if system.startswith("linux"):
        return None
    if system == "darwin" and machine in {"arm64", "aarch64"}:
        return "osx-arm64"
    if system == "win32" and machine in {"amd64", "x86_64"}:
        return "win-64"
    raise UnsupportedRunner(
        f"This runner is {system} {machine}. The CalculiX lock covers osx-arm64 and win-64. "
        "There is no pinned solver for this runner, so the job stops instead of skipping the strength tests."
    )


def github_path_lines(directories: list[Path]) -> list[str]:
    """Lines to append to GITHUB_PATH.

    Each new line is prepended, so the last line written is searched first.
    ``directories`` is in search order: the first entry is the one that contains ccx.
    """

    return [str(path) for path in reversed(directories)]


def solver_started(returncode: int, stdout: str, stderr: str) -> bool:
    """True when the ccx process printed its banner. A missing library does not."""

    if returncode < 0:
        return False
    return "CalculiX" in f"{stdout}{stderr}"


def _download(url: str, dest: Path, expected_sha256: str, expected_size: int) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.is_file() and dest.stat().st_size == expected_size and _sha256(dest) == expected_sha256:
        return
    partial = dest.with_suffix(dest.suffix + ".partial")
    request = urllib.request.Request(url, headers={"User-Agent": "aicad-ci-calculix"})
    last_error: Exception | None = None
    for _attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=120) as response, partial.open("wb") as handle:
                while True:
                    chunk = response.read(1024 * 256)
                    if not chunk:
                        break
                    handle.write(chunk)
            break
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = exc
            partial.unlink(missing_ok=True)
    else:
        raise SystemExit(f"Could not download {url}: {last_error}")
    size = partial.stat().st_size
    digest = _sha256(partial)
    if size != expected_size or digest != expected_sha256:
        partial.unlink(missing_ok=True)
        raise SystemExit(
            f"Checksum failed for {url}: size {size} (expected {expected_size}), sha256 {digest}"
        )
    partial.replace(dest)
    print(f"checked {dest.name} sha256 {digest}")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 256), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _extract_member(archive: Path, member: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "r:bz2") as bundle:
        info = bundle.getmember(member)
        if not info.isfile():
            raise SystemExit(f"{member} is not a file in {archive.name}")
        extracted = bundle.extractfile(info)
        if extracted is None:
            raise SystemExit(f"Could not read {member} from {archive.name}")
        dest.write_bytes(extracted.read())
    dest.chmod(dest.stat().st_mode | stat.S_IEXEC)


def link_environment(base: dict[str, str], package_cache: Path) -> dict[str, str]:
    """Environment for one micromamba link.

    Offline mode reads every package cache and stops if any of them is missing.
    A fresh runner has no ``~/.mamba/pkgs``. Point the process at one cache we
    just created, instead of a runner setting that names a directory that is not there.
    """

    env = dict(base)
    env["CONDA_PKGS_DIRS"] = str(package_cache)
    return env


def _link(micromamba: Path, prefix: Path, root: Path, packages: list[Path]) -> None:
    package_cache = root / "pkgs"
    package_cache.mkdir(parents=True, exist_ok=True)
    command = [
        str(micromamba),
        "--no-rc",
        "--offline",
        "create",
        "-y",
        "-p",
        str(prefix),
        "--root-prefix",
        str(root),
        # The sha256 was checked before this. A local file has no repodata
        # record, so micromamba's own check would only warn.
        "--safety-checks",
        "disabled",
        *[str(path) for path in packages],
    ]
    completed = subprocess.run(
        command,
        check=False,
        text=True,
        env=link_environment(os.environ, package_cache),
    )
    if completed.returncode != 0:
        raise SystemExit(f"micromamba could not link CalculiX (exit {completed.returncode})")


def _find_ccx(prefix: Path, relative_dirs: list[str]) -> Path:
    for relative in relative_dirs:
        for name in ("ccx", "ccx.exe"):
            candidate = prefix / relative / name
            if candidate.is_file():
                return candidate
    raise SystemExit(f"ccx was not linked under {prefix}")


def stage_sibling_dlls(binary: Path, directories: list[Path]) -> list[Path]:
    """Copy DLLs that conda placed away from ccx.exe into its directory.

    Windows searches the executable's directory before PATH. conda-forge puts
    ``libarpack.dll`` in ``Library/mingw-w64/bin``, so ``ccx.exe`` exits with a
    missing-library status until that file sits beside it. An existing file is
    left alone. Other systems do not use this layout.
    """

    if binary.suffix.lower() != ".exe":
        return []
    destination = binary.parent
    copied: list[Path] = []
    for directory in directories:
        if not directory.is_dir() or directory.resolve() == destination.resolve():
            continue
        for source in sorted(directory.glob("*.dll")):
            target = destination / source.name
            if target.exists():
                continue
            shutil.copy2(source, target)
            copied.append(target)
    return copied


def _probe(binary: Path) -> None:
    try:
        completed = subprocess.run(
            [str(binary)],
            check=False,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except subprocess.TimeoutExpired as exc:
        raise SystemExit(f"{binary} did not exit. It did not print the CalculiX banner.") from exc
    except OSError as exc:
        raise SystemExit(f"Could not start {binary}: {exc}") from exc
    stdout = completed.stdout or ""
    stderr = completed.stderr or ""
    if not solver_started(completed.returncode, stdout, stderr):
        sys.stdout.write(stdout)
        sys.stderr.write(stderr)
        raise SystemExit(
            f"{binary} did not start (exit {completed.returncode}). "
            "A missing library looks like this: the banner never prints."
        )
    print(f"CalculiX is installed ({binary}).")


def _publish(directories: list[Path]) -> None:
    lines = github_path_lines(directories)
    github_path = os.environ.get("GITHUB_PATH")
    if github_path:
        with open(github_path, "a", encoding="utf-8", newline="\n") as handle:
            for line in lines:
                handle.write(line + "\n")
    current = os.environ.get("PATH", "")
    # Search order is the reverse of the GITHUB_PATH append order.
    os.environ["PATH"] = os.pathsep.join(reversed(lines)) + (os.pathsep + current if current else "")


def install(lock: dict, *, system: str, machine: str, work: Path, prefix: Path) -> int:
    key = select_platform(system, machine)
    if key is None:
        print("Linux installs CalculiX with install.py. This step does not download it.")
        return 0
    validate_lock(lock)
    spec = lock["platforms"][key]
    micromamba_spec = lock["micromamba"][key]
    work.mkdir(parents=True, exist_ok=True)
    archive = work / "micromamba.tar.bz2"
    _download(micromamba_spec["url"], archive, micromamba_spec["sha256"], micromamba_spec["size"])
    micromamba = work / "micromamba-bin" / micromamba_spec["executable"]
    _extract_member(archive, micromamba_spec["executable"], micromamba)
    packages: list[Path] = []
    for package in spec["packages"]:
        dest = work / "pkgs" / package["filename"]
        _download(package["url"], dest, package["sha256"], package["size"])
        packages.append(dest)
    _link(micromamba, prefix, work / "root", packages)
    directories = [prefix / relative for relative in spec["path"]]
    binary = _find_ccx(prefix, spec["path"])
    for copied in stage_sibling_dlls(binary, directories):
        print(f"placed {copied.name} beside {binary.name}")
    _publish(directories)
    _probe(binary)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Install the pinned CI CalculiX solver.")
    parser.add_argument("--lock", type=Path, default=LOCK_PATH)
    parser.add_argument("--work", type=Path, default=None)
    parser.add_argument("--prefix", type=Path, default=None)
    args = parser.parse_args(argv)
    temp = os.environ.get("RUNNER_TEMP")
    base = Path(temp) if temp else Path.cwd() / ".calculix-ci"
    work = args.work or (base / "work")
    prefix = args.prefix or (base / "prefix")
    try:
        lock = load_lock(args.lock)
        return install(lock, system=sys.platform, machine=platform.machine(), work=work, prefix=prefix)
    except UnsupportedRunner as exc:
        print(exc, file=sys.stderr)
        return 1
    except (OSError, ValueError, json.JSONDecodeError, tarfile.TarError) as exc:
        print(exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
