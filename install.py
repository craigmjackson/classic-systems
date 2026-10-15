#!/usr/bin/env python3
"""Install / verify all dependencies for classic-systems.

Downloads QEMU, MS Network Client 3.0b, and RTL8029 NDIS2 driver;
creates the blank C: drive and share/ scaffolding. DOS 6.22 setup
floppies are NOT auto-downloaded (copyrighted retail media) — place
them manually in systems/<name>/.

Usage:
    python install.py              # full install (default system: dos622)
    python install.py --check      # report missing deps without installing
    python install.py --qemu-only  # only install QEMU (legacy mode)
    python install.py --system-dir systems/dos622
"""

import argparse
import io
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile

ROOT = os.path.dirname(os.path.abspath(__file__))
DEPS_DIR = os.path.join(ROOT, "deps", "qemu", "AMD64")
SYSTEMS_DIR = os.path.join(ROOT, "systems")

# Download sources
W64_INDEX_URL = "https://qemu.weilnetz.de/w64/"
MSNC3_BASE_URL = "https://archive.org/download/msnc3"
MSNC3_FILES = ["Disk1.img", "Disk2.img"]
PCIND_ZIP_URL = "https://archive.org/download/pcind.dos/RTL8029_Ndis2_v2.20.zip"

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; classic-systems-installer)"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _print(msg):
    print(msg, flush=True)


def _download(url, dest, retries=3):
    """Download url to dest with retries and a progress counter (MB)."""
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=300) as resp:
                total = int(resp.headers.get("Content-Length", 0))
                downloaded = 0
                started = time.monotonic()
                with open(dest, "wb") as f:
                    while True:
                        chunk = resp.read(64 * 1024)
                        if not chunk:
                            break
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total and total >= 1024 * 1024:
                            pct = 100 * downloaded // total
                            mb_done = downloaded / (1024 * 1024)
                            mb_total = total / (1024 * 1024)
                            print(
                                f"\r  {mb_done:6.1f} / {mb_total:6.1f} MB ({pct}%)",
                                end="",
                                flush=True,
                            )
                elapsed = time.monotonic() - started
                print(f"  ({elapsed:.0f}s, {downloaded / max(elapsed, 1) / 1024 / 1024:.2f} MB/s)")
            return
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            if os.path.exists(dest):
                try:
                    os.remove(dest)
                except OSError:
                    pass
            if attempt == retries:
                raise
            _print(f"  Attempt {attempt} failed: {e}. Retrying...")


def _build_fat12_image(files, out_path, label="OEMDISK"):
    """Build a 1.44 MB FAT12 floppy image from ``{name: bytes}``.

    ``label`` is the 11-byte volume label written to the boot sector.
    """
    bps, spc, res, nf, rde = 512, 1, 1, 2, 224
    sfat, totsec = 9, 2880
    root_off = (res + nf * sfat) * bps
    data_off = root_off + rde * 32

    items = []
    cluster = 2
    for name, raw in files.items():
        ncl = (len(raw) + bps - 1) // bps
        items.append((name, raw, cluster, ncl))
        cluster += ncl

    fat = bytearray(sfat * bps)

    def fat_set(i, val):
        v = (i * 3) // 2
        if i % 2:
            old = struct.unpack("<H", fat[v:v + 2])[0]
            struct.pack_into("<H", fat, v, (old & 0x00F) | ((val & 0xFFF) << 4))
        else:
            old = struct.unpack("<H", fat[v:v + 2])[0]
            struct.pack_into("<H", fat, v, (old & 0xF000) | (val & 0xFFF))

    fat_set(0, 0xFF9)
    fat_set(1, 0xFFF)
    for _name, _raw, start, ncl in items:
        for j in range(ncl):
            fat_set(start + j, start + j + 1 if j < ncl - 1 else 0xFFF)

    root = bytearray(rde * 32)
    for k, (name, raw, start, _ncl) in enumerate(items):
        stem, dot, ext = name.rpartition(".")
        if not dot:
            stem, ext = name, ""
        e = bytearray(32)
        e[0:8] = stem.encode("cp437")[:8].ljust(8)
        e[8:11] = ext.encode("cp437")[:3].ljust(3)
        e[11] = 0x20
        struct.pack_into("<H", e, 26, start)
        struct.pack_into("<I", e, 28, len(raw))
        root[k * 32:(k + 1) * 32] = e

    boot = bytearray(512)
    boot[0:3] = b"\xeb\x3c\x90"
    boot[3:11] = b"MSDOS5.0"
    struct.pack_into("<H", boot, 11, bps)
    boot[13] = spc
    struct.pack_into("<H", boot, 14, res)
    boot[16] = nf
    struct.pack_into("<H", boot, 17, rde)
    struct.pack_into("<H", boot, 19, totsec)
    boot[21] = 0xF9
    struct.pack_into("<H", boot, 22, sfat)
    struct.pack_into("<H", boot, 24, 18)
    struct.pack_into("<H", boot, 26, 2)
    boot[38] = 0x29
    boot[39:43] = b"\x00\x10\x40\x00"
    boot[43:54] = label.encode("ascii")[:11].ljust(11)
    boot[54:62] = b"FAT12   "
    boot[510] = 0x55
    boot[511] = 0xAA

    out = bytearray(totsec * bps)
    out[0:512] = boot
    for i in range(nf):
        s = res * bps + i * sfat * bps
        out[s:s + sfat * bps] = fat
    out[root_off:root_off + len(root)] = root
    for _name, raw, start, _ncl in items:
        off = data_off + (start - 2) * bps
        out[off:off + len(raw)] = raw

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "wb") as f:
        f.write(bytes(out))


# ---------------------------------------------------------------------------
# Per-dependency checks and installers
# ---------------------------------------------------------------------------

def have_qemu():
    return os.path.isfile(os.path.join(DEPS_DIR, "qemu-system-x86_64.exe"))


def find_latest_qemu_installer():
    """Return the URL of the latest qemu-w64-setup-<date>.exe on weilnetz.de."""
    _print(f"Fetching {W64_INDEX_URL} ...")
    req = urllib.request.Request(W64_INDEX_URL, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=60) as resp:
        html = resp.read().decode("utf-8", errors="replace")
    dates = re.findall(r'href="qemu-w64-setup-(\d+)\.exe"', html, re.IGNORECASE)
    if not dates:
        sys.exit("ERROR: no qemu-w64-setup-*.exe found on the index page.")
    latest = max(dates)
    return W64_INDEX_URL + f"qemu-w64-setup-{latest}.exe"


def ensure_qemu(verbose=True):
    if have_qemu():
        if verbose:
            _print("  QEMU: already installed.")
        return
    if verbose:
        _print("  QEMU: downloading and unpacking ...")
    os.makedirs(DEPS_DIR, exist_ok=True)
    url = find_latest_qemu_installer()
    with tempfile.TemporaryDirectory() as tmp:
        exe_path = os.path.join(tmp, os.path.basename(url))
        _download(url, exe_path)
        for tool in ("7z", "7za", "7zr"):
            sevenzip = shutil.which(tool)
            if sevenzip:
                if verbose:
                    _print(f"  Extracting with {tool} ...")
                subprocess.run(
                    [sevenzip, "x", "-y", f"-o{DEPS_DIR}", exe_path],
                    check=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                return
        if verbose:
            _print("  7-Zip not found; running installer silently ...")
        subprocess.run(
            [exe_path, "/S", f"/D={DEPS_DIR}"],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    if verbose:
        _print("  QEMU: installed.")


def ensure_package_yaml(system_dir, verbose=True):
    if os.path.isfile(os.path.join(system_dir, "package.yaml")):
        return True
    _print(f"""
ERROR: {os.path.join(system_dir, 'package.yaml')} not found.

Create a valid package.yaml in that directory. See AGENTS.md for the
schema. Aborting.
""")
    return False


def have_dos622_floppies(system_dir):
    return all(
        os.path.isfile(os.path.join(system_dir, f"dos622_{i}.img"))
        for i in range(1, 5)
    )


def ensure_dos622_floppies(system_dir, verbose=True):
    if have_dos622_floppies(system_dir):
        if verbose:
            _print("  DOS 6.22 floppies: already present.")
        return True
    _print(f"""
WARNING: DOS 6.22 setup floppies not found in {system_dir}.

Expected files:
  dos622_1.img  dos622_2.img  dos622_3.img  dos622_4.img

These are copyrighted retail media and cannot be auto-downloaded.
Obtain them from your own MS-DOS 6.22 disks or a legally-acquired
ISO image and place them in that directory. Boot will fall through
to the hard disk if they are missing.
""")
    return False


def have_msclient(system_dir):
    return all(
        os.path.isfile(os.path.join(system_dir, "msclient", f))
        for f in MSNC3_FILES
    )


def ensure_msclient(system_dir, verbose=True):
    if have_msclient(system_dir):
        if verbose:
            _print("  MS Network Client: already present.")
        return
    if verbose:
        _print("  MS Network Client: downloading ...")
    msclient_dir = os.path.join(system_dir, "msclient")
    os.makedirs(msclient_dir, exist_ok=True)
    for fname in MSNC3_FILES:
        url = f"{MSNC3_BASE_URL}/{fname}"
        dest = os.path.join(msclient_dir, fname)
        _download(url, dest)
    if verbose:
        _print("  MS Network Client: installed.")


def have_pcind(system_dir):
    return os.path.isfile(os.path.join(system_dir, "msclient", "pcind.img"))


def ensure_pcind(system_dir, verbose=True):
    if have_pcind(system_dir):
        if verbose:
            _print("  RTL8029 driver: already present.")
        return
    if verbose:
        _print("  RTL8029 driver: downloading and building ...")
    msclient_dir = os.path.join(system_dir, "msclient")
    os.makedirs(msclient_dir, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        zip_path = os.path.join(tmp, "pcind.zip")
        _download(PCIND_ZIP_URL, zip_path)
        files = {}
        with zipfile.ZipFile(zip_path) as z:
            for name in ("DOS/PCIND.DOS", "DOS/RTL8029.NIF", "DOS/OEMSETUP.INF"):
                if name in z.namelist():
                    short = name.split("/")[-1].upper()
                    files[short] = z.read(name)
        out = os.path.join(msclient_dir, "pcind.img")
        _build_fat12_image(files, out, label="PCIND")
    if verbose:
        _print("  RTL8029 driver: installed.")


def have_c_drive(system_dir):
    c_path = os.path.join(system_dir, "dos622_c.img")
    if not os.path.isfile(c_path):
        return False
    return os.path.getsize(c_path) >= 2 * 1024 * 1024 * 1024 - 1024 * 1024


def ensure_c_drive(system_dir, verbose=True):
    if have_c_drive(system_dir):
        size = os.path.getsize(os.path.join(system_dir, "dos622_c.img"))
        if verbose:
            _print(f"  C: drive: already present ({size // 1024 // 1024} MB).")
        return
    if not have_qemu():
        ensure_qemu(verbose=False)
    qemu_img = os.path.join(DEPS_DIR, "qemu-img.exe")
    if not os.path.isfile(qemu_img):
        _print("ERROR: qemu-img.exe not found in deps/qemu/AMD64/.")
        _print("       Run install.py to install QEMU first.")
        return
    c_path = os.path.join(system_dir, "dos622_c.img")
    if verbose:
        _print("  C: drive: creating 2 GB raw image ...")
    subprocess.run(
        [qemu_img, "create", "-f", "raw", c_path, "2048M"],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if verbose:
        _print("  C: drive: created.")


def have_share(system_dir):
    return os.path.isfile(os.path.join(system_dir, "share", "HELLO.TXT"))


def ensure_share(system_dir, verbose=True):
    hello_path = os.path.join(system_dir, "share", "HELLO.TXT")
    if os.path.isfile(hello_path):
        if verbose:
            _print("  share/: already populated.")
        return
    os.makedirs(os.path.dirname(hello_path), exist_ok=True)
    if verbose:
        _print("  share/: creating HELLO.TXT ...")
    with open(hello_path, "w", encoding="utf-8") as f:
        f.write(
            "Hello from the Windows host filesystem!\n"
            "If you can read this in DOS as F:\\HELLO.TXT, the SMB share works.\n"
        )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def check_deps(system_dir):
    """Return an ordered dict-like list of ``(name, ok)`` pairs."""
    return [
        ("QEMU", have_qemu()),
        ("package.yaml", os.path.isfile(os.path.join(system_dir, "package.yaml"))),
        ("DOS 6.22 floppies", have_dos622_floppies(system_dir)),
        ("C: drive", have_c_drive(system_dir)),
        ("MS Network Client", have_msclient(system_dir)),
        ("RTL8029 driver", have_pcind(system_dir)),
        ("share/HELLO.TXT", have_share(system_dir)),
    ]


def ensure_all(system_dir=None, verbose=True):
    """Ensure every dependency is present; download/build as needed.

    ``system_dir`` defaults to ``systems/dos622`` under the repo root.

    Returns True if **all** deps are satisfied (including DOS floppies),
    False only when the DOS 6.22 floppies are missing (they must be
    provided manually). Raises on hard failures (network error, bad
    package.yaml, etc.).
    """
    if system_dir is None:
        system_dir = os.path.join(SYSTEMS_DIR, "dos622")

    if verbose:
        _print(f"Checking dependencies for {os.path.relpath(system_dir, ROOT)} ...")
        for name, ok in check_deps(system_dir):
            _print(f"  {name}: {'OK' if ok else 'MISSING'}")

    if not ensure_package_yaml(system_dir, verbose=verbose):
        return False

    ensure_qemu(verbose=verbose)
    ensure_c_drive(system_dir, verbose=verbose)
    ensure_msclient(system_dir, verbose=verbose)
    ensure_pcind(system_dir, verbose=verbose)
    ensure_share(system_dir, verbose=verbose)

    dos_ok = have_dos622_floppies(system_dir)

    if verbose:
        if dos_ok:
            _print("\nAll dependencies satisfied.")
        else:
            _print("\nNote: DOS 6.22 floppies are still missing (user-provided).")
            _print("      Boot will fall through to the hard disk.")
    return dos_ok


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Install / verify classic-systems dependencies.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Report missing dependencies without installing anything.",
    )
    parser.add_argument(
        "--qemu-only",
        action="store_true",
        help="Only install QEMU (legacy single-dependency mode).",
    )
    parser.add_argument(
        "--system-dir",
        default=None,
        help=f"Override system directory (default: {os.path.join(SYSTEMS_DIR, 'dos622')})",
    )
    args = parser.parse_args()

    system_dir = args.system_dir or os.path.join(SYSTEMS_DIR, "dos622")

    if args.check:
        all_ok = True
        for name, ok in check_deps(system_dir):
            _print(f"  {name}: {'OK' if ok else 'MISSING'}")
            if not ok:
                all_ok = False
        sys.exit(0 if all_ok else 1)

    if args.qemu_only:
        ensure_qemu()
        return

    ok = ensure_all(system_dir)
    sys.exit(0 if ok else 2)


if __name__ == "__main__":
    main()
