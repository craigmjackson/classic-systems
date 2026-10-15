import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request

W64_INDEX_URL = "https://qemu.weilnetz.de/w64/"
DEST_DIR = os.path.join("deps", "qemu", "AMD64")
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; qemu-installer)"}


def fetch(url):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read()


def find_latest_installer():
    print(f"Fetching {W64_INDEX_URL} ...")
    html = fetch(W64_INDEX_URL).decode("utf-8", errors="replace")
    dates = re.findall(r'href="qemu-w64-setup-(\d+)\.exe"', html, re.IGNORECASE)
    if not dates:
        sys.exit("ERROR: no qemu-w64-setup-*.exe found on the index page.")
    latest = max(dates)
    name = f"qemu-w64-setup-{latest}.exe"
    print(f"Latest installer: {name}")
    return W64_INDEX_URL + name


def download(url, dest):
    print(f"Downloading {url} ...")
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=600) as resp, open(dest, "wb") as f:
        shutil.copyfileobj(resp, f)
    print(f"Saved to {dest}")


def unpack(exe_path, dest_dir):
    for tool in ("7z", "7za", "7zr"):
        sevenzip = shutil.which(tool)
        if sevenzip:
            print(f"Extracting into {os.path.abspath(dest_dir)} with {tool} ...")
            subprocess.run([sevenzip, "x", "-y", f"-o{os.path.abspath(dest_dir)}", exe_path], check=True)
            return
    print("7-Zip not found; running installer silently ...")
    subprocess.run([exe_path, "/S", f"/D={os.path.abspath(dest_dir)}"], check=True)


def main():
    os.makedirs(DEST_DIR, exist_ok=True)
    url = find_latest_installer()
    with tempfile.TemporaryDirectory() as tmp:
        exe_path = os.path.join(tmp, os.path.basename(url))
        download(url, exe_path)
        unpack(exe_path, DEST_DIR)
    print("Done.")


if __name__ == "__main__":
    main()
