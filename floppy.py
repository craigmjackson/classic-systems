import argparse
import json
import os
import socket
import sys

import yaml

ROOT = os.path.dirname(os.path.abspath(__file__))
SYSTEMS_DIR = os.path.join(ROOT, "systems")

DEVICE_MAP = {"fda": "floppy0", "fdb": "floppy1"}


def load_package(name):
    path = os.path.join(SYSTEMS_DIR, name, "package.yaml")
    if not os.path.isfile(path):
        sys.exit(f"ERROR: no package.yaml for system '{name}'")
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def floppy_device(pkg):
    for drive in pkg.get("qemu", {}).get("drives", []):
        if drive.get("interface") == "floppy":
            return DEVICE_MAP.get(drive.get("device", "fda"), "floppy0")
    return "floppy0"


def remove_floppy_from_package(name, pkg):
    drives = pkg.get("qemu", {}).get("drives", [])
    remaining = [d for d in drives if d.get("interface") != "floppy"]
    if len(remaining) == len(drives):
        return False
    pkg["qemu"]["drives"] = remaining
    write_package(name, pkg)
    return True


def add_floppy_to_package(name, pkg, image):
    drives = pkg.get("qemu", {}).get("drives", [])
    if any(d.get("interface") == "floppy" for d in drives):
        return False
    pkg["qemu"]["drives"] = [{"interface": "floppy", "device": "fda", "file": image}] + drives
    write_package(name, pkg)
    return True


def write_package(name, pkg):
    path = os.path.join(SYSTEMS_DIR, name, "package.yaml")
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(pkg, f, sort_keys=False, default_flow_style=False)


class Qmp:
    def __init__(self, host, port):
        self.sock = socket.create_connection((host, port), timeout=10)
        self.f = self.sock.makefile("rw", encoding="utf-8")
        self.f.readline()
        self.send({"execute": "qmp_capabilities"})

    def send(self, cmd):
        self.sock.sendall((json.dumps(cmd) + "\n").encode("utf-8"))
        while True:
            line = self.f.readline()
            if not line:
                raise ConnectionError("QMP connection closed")
            reply = json.loads(line)
            if "return" in reply or "error" in reply:
                return reply

    def close(self):
        self.sock.close()


def main():
    parser = argparse.ArgumentParser(description="Eject or swap the floppy image of a running QEMU system.")
    parser.add_argument("system")
    parser.add_argument("command", choices=["eject", "insert", "query"])
    parser.add_argument("image", nargs="?", help="image file (relative to the system dir) for insert")
    args = parser.parse_args()

    pkg = load_package(args.system)
    qemu = pkg.get("qemu", {})
    port = qemu.get("qmp_port")
    if not port:
        sys.exit("ERROR: qemu.qmp_port not set in package.yaml")
    device = floppy_device(pkg)

    try:
        qmp = Qmp("127.0.0.1", port)
    except OSError:
        sys.exit(f"ERROR: cannot connect to QMP on 127.0.0.1:{port}. Is the VM running?")

    if args.command == "eject":
        reply = qmp.send({"execute": "eject", "arguments": {"device": device, "force": True}})
        if "return" in reply and remove_floppy_from_package(args.system, pkg):
            print(f"Removed floppy entry from systems/{args.system}/package.yaml")
    elif args.command == "insert":
        if not args.image:
            qmp.close()
            sys.exit("ERROR: insert requires an image filename")
        path = os.path.abspath(os.path.join(SYSTEMS_DIR, args.system, args.image))
        if not os.path.isfile(path):
            qmp.close()
            sys.exit(f"ERROR: image not found: {path}")
        reply = qmp.send(
            {
                "execute": "blockdev-change-medium",
                "arguments": {"device": device, "filename": path, "force": True},
            }
        )
        if "return" in reply:
            system_dir = os.path.join(SYSTEMS_DIR, args.system)
            rel = os.path.relpath(path, system_dir)
            record = path if rel.startswith("..") else rel.replace(os.sep, "/")
            if add_floppy_to_package(args.system, pkg, record):
                print(f"Added floppy entry to systems/{args.system}/package.yaml")
    else:
        reply = qmp.send({"execute": "query-block"})

    qmp.close()

    if "error" in reply:
        print(json.dumps(reply["error"], indent=2))
        sys.exit(1)
    if args.command == "query":
        print(json.dumps(reply["return"], indent=2))
    else:
        print(f"OK: {args.command} on {device}")


if __name__ == "__main__":
    main()
