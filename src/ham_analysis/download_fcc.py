"""Download and extract the FCC ULS amateur license dump."""

from __future__ import annotations

import zipfile
from datetime import datetime
from pathlib import Path

import httpx

from ham_analysis.config import (
    FCC_AMAT_LICENSE_URL,
    FCC_AMAT_ZIP_NAME,
    FCC_AMAT_APPLICATION_URL,
    FCC_AMAT_APPLICATION_ZIP_NAME,
    FCC_RAW_DIR,
    ensure_dirs,
)

# Files we need from l_amat.zip
NEEDED_DAT_FILES = ("HD.dat", "EN.dat", "AM.dat")


def download_file(url: str, dest: Path, *, force: bool = False) -> Path:
    """Stream-download url to dest unless it already exists."""
    ensure_dirs()
    if dest.exists() and not force:
        print(f"  Using cached {dest}")
        return dest

    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".partial")
    print(f"  Downloading {url}")
    print(f"  → {dest}")

    with httpx.stream("GET", url, follow_redirects=True, timeout=600.0) as resp:
        resp.raise_for_status()
        total = int(resp.headers.get("content-length") or 0)
        written = 0
        with tmp.open("wb") as f:
            for chunk in resp.iter_bytes(chunk_size=1024 * 1024):
                f.write(chunk)
                written += len(chunk)
                if total:
                    pct = 100.0 * written / total
                    print(f"\r  {written / 1e6:.1f} / {total / 1e6:.1f} MB ({pct:.0f}%)", end="")
                else:
                    print(f"\r  {written / 1e6:.1f} MB", end="")
        print()

    tmp.replace(dest)
    return dest


def extract_uls_files(
    zip_path: Path, extract_dir: Path, *, force: bool = False,
    needed_files: tuple[str, ...] = NEEDED_DAT_FILES,
) -> dict[str, Path]:
    """Extract HD/EN/AM .dat files from the amateur license zip."""
    extract_dir.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}

    # Zip may use HD.dat or hd.dat depending on platform
    with zipfile.ZipFile(zip_path, "r") as zf:
        name_map = {name.upper(): name for name in zf.namelist()}
        for needed in needed_files:
            archive_name = name_map.get(needed.upper())
            if not archive_name:
                raise FileNotFoundError(f"{needed} not found in {zip_path}")
            out = extract_dir / needed
            if out.exists() and not force:
                print(f"  Using cached {out}")
            else:
                print(f"  Extracting {archive_name} → {out}")
                with zf.open(archive_name) as src, out.open("wb") as dst:
                    while True:
                        chunk = src.read(1024 * 1024)
                        if not chunk:
                            break
                        dst.write(chunk)
            paths[needed] = out

    return paths


def download_fcc(*, force: bool = False) -> dict[str, Path]:
    """Download licenses and applications from the same weekly release cycle."""
    ensure_dirs()
    print("FCC ULS amateur licenses")
    zip_path = FCC_RAW_DIR / FCC_AMAT_ZIP_NAME
    app_zip = FCC_RAW_DIR / FCC_AMAT_APPLICATION_ZIP_NAME
    # Introducing application data must also refresh an older license cache.
    refresh = force or not zip_path.exists() or not app_zip.exists()
    download_file(FCC_AMAT_LICENSE_URL, zip_path, force=refresh)
    download_file(FCC_AMAT_APPLICATION_URL, app_zip, force=refresh)
    with zipfile.ZipFile(zip_path) as licenses, zipfile.ZipFile(app_zip) as apps:
        license_hd = next(i for i in licenses.infolist() if i.filename.upper() == "HD.DAT")
        app_hd = next(i for i in apps.infolist() if i.filename.upper() == "HD.DAT")
        license_date = datetime(*license_hd.date_time).date()
        app_date = datetime(*app_hd.date_time).date()
    if abs((license_date - app_date).days) > 2:
        raise ValueError("FCC license/application releases differ by more than two days. "
                         "Retry `ham download-fcc --force` when both weekly files are ready.")
    extract_dir = FCC_RAW_DIR / "extract"
    paths = extract_uls_files(zip_path, extract_dir, force=refresh)
    app_paths = extract_uls_files(
        app_zip, FCC_RAW_DIR / "applications", force=refresh,
        needed_files=("HD.dat", "AD.dat", "EN.dat"),
    )
    paths.update({f"application_{name}": path for name, path in app_paths.items()})
    return paths
