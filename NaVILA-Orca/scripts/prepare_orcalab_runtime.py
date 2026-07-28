#!/usr/bin/env python3
"""Prepare OrcaLab's native viewport before the first GUI process starts."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import sysconfig
import tarfile
import tempfile
import tomllib
from urllib.request import urlopen


ORCALAB_VERSION = "26.6.3"
PYSIDE_URL = (
    "https://orcalab-open.oss-cn-shanghai.aliyuncs.com/"
    "python-project_linux.26.6.3.tar.xz"
)
PYSIDE_SHA256 = "f8f110b078604c0e529285378b1e7bd8e15a0e773edde0a012c33c82012d7df6"
PAK_URL = (
    "https://orcalab-open.oss-cn-shanghai.aliyuncs.com/"
    "orcalab_linux.26.6.3.pak"
)
PAK_SHA256 = "938ed92bb5f94476090dd4f1a9c820ba6c5c6ca410d6af285918d23cd73f06d0"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download_verified(url: str, destination: Path, expected_sha256: str) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_file() and sha256(destination) == expected_sha256:
        print(f"[orcalab-runtime] verified cached {destination.name}")
        return

    temporary = destination.with_name(f".{destination.name}.download")
    temporary.unlink(missing_ok=True)
    print(f"[orcalab-runtime] downloading {url}")
    try:
        with urlopen(url, timeout=60) as response, temporary.open("wb") as output:
            shutil.copyfileobj(response, output, length=1024 * 1024)
        actual = sha256(temporary)
        if actual != expected_sha256:
            raise RuntimeError(
                f"SHA256 mismatch for {destination.name}: "
                f"found {actual}, expected {expected_sha256}"
            )
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def editable_root(root: Path) -> Path | None:
    candidates = [root, *sorted(path.parent for path in root.rglob("pyproject.toml"))]
    for candidate in candidates:
        pyproject = candidate / "pyproject.toml"
        if not pyproject.is_file():
            continue
        with pyproject.open("rb") as stream:
            project = tomllib.load(stream).get("project", {})
        if (
            project.get("name") == "orcalab-pyside"
            and project.get("version") == ORCALAB_VERSION
        ):
            return candidate
    return None


def extract_runtime(archive: Path, destination: Path) -> Path:
    existing = editable_root(destination) if destination.is_dir() else None
    if existing is not None:
        print(f"[orcalab-runtime] verified extracted {existing}")
        return existing

    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(prefix=".orcalab-pyside-prepare-", dir=destination.parent)
    )
    try:
        with tarfile.open(archive, mode="r:xz") as package:
            package.extractall(staging, filter="data")
        if editable_root(staging) is None:
            raise RuntimeError("official OrcaLab archive contains no 26.6.3 Python project")
        if destination.exists():
            shutil.rmtree(destination)
        staging.rename(destination)
    finally:
        if staging.exists():
            shutil.rmtree(staging)

    root = editable_root(destination)
    if root is None:  # pragma: no cover - guarded before the atomic rename
        raise RuntimeError("failed to prepare the OrcaLab Python project")
    return root


def patch_native_rpath(root: Path) -> None:
    native_library = (
        root / "src" / "orcalab_pyside" / "dist" / "OrcaPySide.so"
    )
    patchelf = Path(sys.prefix) / "bin" / "patchelf"
    if not native_library.is_file():
        raise RuntimeError(f"OrcaLab native viewport is missing: {native_library}")
    if not patchelf.is_file():
        raise RuntimeError(f"pinned patchelf executable is missing: {patchelf}")

    import PySide6

    pyside6 = Path(PySide6.__file__).resolve().parent
    python_lib = Path(sysconfig.get_config_var("LIBDIR")).resolve()
    dist = native_library.parent.resolve()
    rpath = f"$ORIGIN:{pyside6}:{python_lib}:{dist}"
    subprocess.check_call(
        [str(patchelf), "--set-rpath", rpath, str(native_library)]
    )
    actual = subprocess.check_output(
        [str(patchelf), "--print-rpath", str(native_library)], text=True
    ).strip()
    if actual != rpath:
        raise RuntimeError(
            f"OrcaLab native viewport RPATH mismatch: {actual!r} != {rpath!r}"
        )
    print("[orcalab-runtime] native viewport RPATH verified")


def main() -> int:
    if sys.platform != "linux":
        raise SystemExit("Orca_VLN currently supports the OrcaLab runtime on Linux")
    if len(sys.argv) != 2:
        raise SystemExit("usage: prepare_orcalab_runtime.py CONSTRAINTS_FILE")

    constraints = Path(sys.argv[1]).resolve()
    if not constraints.is_file():
        raise SystemExit(f"constraints file does not exist: {constraints}")

    from orcalab.project_util import get_cache_folder, project_id

    user_root = (
        Path.home() / "Orca" / "OrcaStudio" / project_id / "user"
    )
    # OrcaLab 26.6.3's own URL parser names this release "unknown". Keep the
    # same paths and state value so its first GUI process recognizes the
    # preinstalled official runtime instead of installing it again.
    url_version = "unknown"
    archive = user_root / f"python-project-{url_version}.tar.xz"
    destination = user_root / "orcalab-pyside"
    state_file = user_root / ".orcalab-pyside-install-state.json"
    pak = Path(get_cache_folder()) / Path(PAK_URL).name

    download_verified(PYSIDE_URL, archive, PYSIDE_SHA256)
    root = extract_runtime(archive, destination)
    subprocess.check_call(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--constraint",
            str(constraints),
            "--editable",
            str(root),
        ]
    )
    patch_native_rpath(root)
    download_verified(PAK_URL, pak, PAK_SHA256)

    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(
        json.dumps(
            {
                "installed_url": PYSIDE_URL,
                "installed_path": None,
                "url_version": url_version,
                "installed_at": str(Path.cwd()),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print("[orcalab-runtime] native viewport and pak are ready")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
