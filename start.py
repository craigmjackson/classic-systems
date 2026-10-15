import argparse
import os
import socket
import subprocess
import sys
import time

import yaml

import install as _install

ROOT = os.path.dirname(os.path.abspath(__file__))
SYSTEMS_DIR = os.path.join(ROOT, "systems")
QEMU_DIR = os.path.join(ROOT, "deps", "qemu", "AMD64")


def discover_systems():
    if not os.path.isdir(SYSTEMS_DIR):
        return []
    found = []
    for name in sorted(os.listdir(SYSTEMS_DIR)):
        if os.path.isfile(os.path.join(SYSTEMS_DIR, name, "package.yaml")):
            found.append(name)
    return found


def load_package(name):
    path = os.path.join(SYSTEMS_DIR, name, "package.yaml")
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def ensure_disk_image(path, size):
    if os.path.exists(path):
        return
    qemu_img = os.path.join(QEMU_DIR, "qemu-img.exe")
    print(f"Creating disk image {path} ({size}) ...")
    subprocess.run([qemu_img, "create", "-f", "raw", path, size], check=True)


def build_command(name, pkg):
    qemu = pkg.get("qemu", {})
    system_dir = os.path.join(SYSTEMS_DIR, name)
    binary = os.path.join(QEMU_DIR, qemu.get("binary", "qemu-system-x86_64.exe"))
    if not os.path.isfile(binary):
        sys.exit(f"ERROR: {binary} not found. Run install.py first.")

    cmd = [binary, "-name", name]
    if qemu.get("machine"):
        cmd += ["-machine", qemu["machine"]]
    if qemu.get("cpus"):
        cmd += ["-smp", str(qemu["cpus"])]
    if qemu.get("memory_mb"):
        cmd += ["-m", str(qemu["memory_mb"])]
    drives = qemu.get("drives", [])
    has_floppy = any(d.get("interface") == "floppy" for d in drives)
    boot_order = qemu.get("boot_order")
    if boot_order and not has_floppy:
        boot_order = "".join(c for c in boot_order if c not in "ab")
    if boot_order:
        cmd += ["-boot", f"order={boot_order}"]
    if qemu.get("qmp_port"):
        cmd += ["-qmp", f"tcp:127.0.0.1:{qemu['qmp_port']},server,nowait"]

    ide_index = 0
    floppy_present = False
    for drive in drives:
        image = os.path.join(system_dir, drive["file"])
        interface = drive.get("interface", "ide")
        if interface == "floppy":
            cmd += [f"-{drive.get('device', 'fda')}", image]
            floppy_present = True
        else:
            ensure_disk_image(image, drive.get("size", "2048M"))
            cmd += [
                "-drive",
                f"if=ide,index={ide_index},media=disk,file={image}",
            ]
            ide_index += 1

    if not floppy_present:
        cmd += ["-fda", ""]

    net = pkg.get("network", {})
    if net.get("nic"):
        dev = net["nic"]
        for key in ("iobase", "irq", "mac"):
            if net.get(key) is not None:
                dev += f",{key}={net[key]}"
        dev += ",netdev=net0"
        if net.get("smb_dir"):
            share = os.path.abspath(os.path.join(system_dir, net["smb_dir"]))
            os.makedirs(share, exist_ok=True)
            cmd += ["-netdev", "user,id=net0", "-device", dev]
        else:
            cmd += ["-netdev", "user,id=net0", "-device", dev]

    cmd += qemu.get("args", [])
    return cmd


def main():
    parser = argparse.ArgumentParser(description="Start a configured system in QEMU.")
    parser.add_argument("system", nargs="?", help="system name (omit to list systems)")
    parser.add_argument(
        "--skip-deps",
        action="store_true",
        help="Don't auto-install missing dependencies (assumes install.py was run).",
    )
    args = parser.parse_args()

    systems = discover_systems()
    if not args.system:
        if not systems:
            print("No systems found under systems/<name>/package.yaml")
        else:
            print("Available systems:")
            for name in systems:
                print(f"  - {name}")
        return

    if args.system not in systems:
        sys.exit(f"ERROR: unknown system '{args.system}'. Available: {', '.join(systems) or 'none'}")

    system_dir = os.path.join(SYSTEMS_DIR, args.system)

    if not args.skip_deps:
        try:
            deps_ok = _install.ensure_all(system_dir, verbose=True)
            if not deps_ok:
                print(
                    "\nNote: DOS 6.22 setup floppies are missing — "
                    "boot will fall through to the hard disk.\n"
                )
        except Exception as e:
            print(f"Warning: dependency check failed: {e}")
            print("Continuing anyway. You may need to run `python install.py` manually.\n")

    pkg = load_package(args.system)
    cmd = build_command(args.system, pkg)
    print("Launching: " + " ".join(cmd))
    net = pkg.get("network", {})
    if net.get("smb_dir"):
        system_dir = os.path.join(SYSTEMS_DIR, args.system)
        share = os.path.abspath(os.path.join(system_dir, net["smb_dir"]))
        port = int(net.get("smb_port", 139))
        smb_name = net.get("smb_name", "HOST")
        log_path = os.path.join(system_dir, "smb_host.log")
        smb_script = os.path.join(ROOT, "smb_host.py")
        smb_cmd = [sys.executable, smb_script, "--dir", share, "--name", smb_name,
                   "--port", str(port), "--log", log_path]
        smb_proc = subprocess.Popen(smb_cmd, cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                if smb_proc.poll() is not None:
                    sys.exit(f"ERROR: SMB share server exited with code {smb_proc.returncode}; see {log_path}")
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                        break
                except OSError:
                    time.sleep(0.1)
            else:
                sys.exit(f"ERROR: SMB share server did not listen on port {port}; see {log_path}")
            subprocess.run(cmd, check=False)
        finally:
            if smb_proc.poll() is None:
                smb_proc.terminate()
                try:
                    smb_proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    smb_proc.kill()
                    smb_proc.wait()
    else:
        subprocess.run(cmd, check=False)


if __name__ == "__main__":
    main()
