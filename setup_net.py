#!/usr/bin/env python3
"""Install the MS Network Client stack into a DOS disk image (offline).

Reads systems/<name>/msclient floppy images, expands the SZDD-compressed
MS Network Client files, and writes the complete network configuration
(SYSTEM.INI, PROTOCOL.INI, LMHOSTS, NETMAP.BAT) plus the known-good
AUTOEXEC.BAT / CONFIG.SYS into the C: drive image — without booting QEMU.

Usage:
    python setup_net.py dos622              # install (VM must be off)
    python setup_net.py dos622 --check      # report installed/not (exit 0/1)
    python setup_net.py dos622 --dry-run    # decompress + plan, touch nothing
    python setup_net.py dos622 --force      # overwrite existing C:\\NET
    python setup_net.py dos622 --test       # boot headless + verify F: end-to-end
                                            # (auto-skips write if already installed)
"""

import argparse
import base64
import json
import os
import socket
import struct
import subprocess
import sys
import time
from datetime import datetime

import yaml

ROOT = os.path.dirname(os.path.abspath(__file__))
SYSTEMS_DIR = os.path.join(ROOT, "systems")

SZDD_SIG = b"SZDD\x88\xF0\x27\x33"

COMPUTERNAME = "DOS622"
USERNAME = "DOS622"
WORKGROUP = "WORKGROUP"
GUEST_IP = "10.0.2.15"
GUEST_NETMASK = "255.255.255.0"
GATEWAY = "10.0.2.2"
LMHOSTS_NAME = "smbserver"   # preloaded name the guest resolves to 10.0.2.2

# Stock WFWSYS.CFG (840 bytes) from the Windows NT Server 4.0 CD,
# clients\msclient\netsetup\ — NET.EXE requires it in C:\NET or
# "net initialize" fails with Error 2 + "security-settings file" errors
# (KB Q168960). It is the seed from which SHARES.PWL is rebuilt on
# first boot. Binary format (starts with 0xB0 'MFN').
WFWSYS_CFG_B64 = (
    "sE1GTgIAAAATAA0SERAPDgwLCgkIBwYFBAMCAQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAIEBwgJCgsFBgoBAgMFBgcIAgf/"
    "////////////////////////////////////////////////////////////////////////////"
    "////////////////////////////////////////////////////////////////////////////"
    "////////////////////////////////////////////////////////////////////////////"
    "////////////////////////////////////////////////////////////////////////////"
    "/////////2PPGcmiX/Hdo+D/4amO7lQWGPoo3sEigM9ilphETgtujk6VkxLjw/HgoOD03I0cVFD0"
    "b0m/FzCWPJZIneUS+9iS4M/hqY48lkid5RLx2OHgz+GljuxUFBjxLdLDU4KNYOCawUyIaWxMXxPi"
    "4T6WSJ3kEv3YkODO0qkA7lQ8lkid5RLw2Ofgz+GpjjyWSJ3lEvbY9uDP4bmO7FQQGPct1cNTs7BZ"
    "25rATDyWSJ3lEvnY9eDP4aWO7FQUGPQt1sNSgoFgPJZIneUS89jk4M/hpY7sVBcY9S23w2JjjWDg"
    "msFMi2kJTF/y4uE8lkid5RLy2OvgzuGljuxUFxjqLbjDYmOBYDyWSJ3lEvXY6uDP4amOPJZIneUS"
    "9Njp4M/hpY7sVBQY8y26w1OCgWA8lkid5RL32Ojgz+GpjjCWMJYwljCW"
)

# Stock SHARES.PWL (622 bytes) from the same NT Server CD directory. Shipping
# the matched pair skips net start's first-boot rebuild of the password list
# from WFWSYS.CFG — that rebuild path hangs the DOS redirector startup here.
SHARES_PWL_B64 = (
    "sE1GTgEAAAABAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAX/////////////////////////"
    "////////////////////////////////////////////////////////////////////////////"
    "////////////////////////////////////////////////////////////////////////////"
    "////////////////////////////////////////////////////////////////////////////"
    "////////////////////////////////////////////////////////////////////////////"
    "//////////RL4SDRUi46igYeZ1dukm3HGn+jzs6rlQcpCTBhlBec8s4pBTOd2ccjeMHJVv5KG+E2"
    "9C/VYN4Y3hjeGN4Y3hjOGItBpTddP78GL1ZmV6ttxxreGN4Y3hjeGN4Y3hjeGN4Y3hjeGA=="
)

# DOS622.PWL (606 bytes): user password list created by the first logon
# (user DOS622, empty password). Without it, autologon still shows the
# "Type your user name" prompt at net start and headless boots hang there.
DOS622_PWL_B64 = (
    "sE1GTgAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAP//////////////////////////"
    "////////////////////////////////////////////////////////////////////////////"
    "////////////////////////////////////////////////////////////////////////////"
    "////////////////////////////////////////////////////////////////////////////"
    "////////////////////////////////////////////////////////////////////////////"
    "/////////5pX2neRBV06igYeZ1dukm3HGn+jzs6rlQcpCTBhlBec4s45BSOd6ccTePHJZv56G9E2"
    "xC/lYN4Y3hjeGN4Y3hjeGN4Y3hjeGN4Y3hjeGN4Y3hjeGN4Y"
)

# (floppy image name, stored name, destination name under C:\NET unless absolute)
MANIFEST = [
    ("Disk1.img", "NET.EX_", "NET.EXE"),
    ("Disk1.img", "NET.MS_", "NET.MSG"),
    ("Disk1.img", "NETH.MS_", "NETH.MSG"),
    ("Disk1.img", "NETBIND.COM", "NETBIND.COM"),
    ("Disk1.img", "PROTMAN.DO_", "PROTMAN.DOS"),
    ("Disk1.img", "PROTMAN.EX_", "PROTMAN.EXE"),
    ("Disk1.img", "IFSHLP.SY_", "IFSHLP.SYS"),
    ("Disk1.img", "NDISHLP.SY_", "NDISHLP.SYS"),
    ("Disk2.img", "UMB.CO_", "UMB.COM"),
    ("Disk2.img", "TCPTSR.EX_", "TCPTSR.EXE"),
    ("Disk2.img", "TINYRFC.EX_", "TINYRFC.EXE"),
    ("Disk2.img", "NMTSR.EX_", "NMTSR.EXE"),
    ("Disk2.img", "EMSBFR.EX_", "EMSBFR.EXE"),
    ("Disk2.img", "TCPDRV.DO_", "TCPDRV.DOS"),
    ("Disk2.img", "NEMM.DO_", "NEMM.DOS"),
    ("Disk2.img", "TCPUTILS.INI", "TCPUTILS.INI"),
    ("Disk2.img", "ADDNAME.EX_", "ADDNAME.EXE"),
    ("Disk2.img", "PING.EX_", "PING.EXE"),
    ("Disk2.img", "SOCKETS.EX_", "SOCKETS.EXE"),
    ("Disk2.img", "DNR.EX_", "DNR.EXE"),
    ("Disk2.img", "IPCONFIG.EX_", "IPCONFIG.EXE"),
    ("Disk2.img", "HOSTS", "HOSTS"),
    ("Disk2.img", "NETWORKS", "NETWORKS"),
    ("Disk2.img", "PROTOCOL", "PROTOCOL"),
    ("Disk2.img", "SERVICES", "SERVICES"),
    ("pcind.img", "PCIND.DOS", "PCIND.DOS"),
    ("pcind.img", "RTL8029.NIF", "RTL8029.NIF"),
]


class SetupError(Exception):
    pass


# ---------------------------------------------------------------------------
# SZDD decompression (Microsoft COMPRESS.EXE "A" format)
# ---------------------------------------------------------------------------

def szdd_decompress(raw, name=""):
    if raw[:8] != SZDD_SIG:
        raise SetupError(f"{name}: not an SZDD compressed file (magic {raw[:8]!r})")
    if raw[8] != 0x41:
        raise SetupError(f"{name}: unsupported SZDD compression mode 0x{raw[8]:02X}")
    size = struct.unpack_from("<I", raw, 10)[0]
    window = bytearray(b"\x20" * 4096)
    wpos = 4096 - 16
    out = bytearray()
    i = 14
    n = len(raw)
    while i < n and len(out) < size:
        control = raw[i]
        i += 1
        bit = 1
        while bit <= 0x80 and i <= n:
            if len(out) >= size:
                break
            if control & bit:
                if i >= n:
                    raise SetupError(f"{name}: truncated SZDD literal")
                b = raw[i]
                i += 1
                window[wpos] = b
                out.append(b)
                wpos = (wpos + 1) & 4095
            else:
                if i + 1 >= n:
                    raise SetupError(f"{name}: truncated SZDD match")
                b1 = raw[i]
                b2 = raw[i + 1]
                i += 2
                mpos = (b1 | ((b2 & 0xF0) << 4)) & 4095
                mlen = (b2 & 0x0F) + 3
                for _ in range(mlen):
                    if len(out) >= size:
                        break
                    v = window[mpos]
                    window[wpos] = v
                    out.append(v)
                    wpos = (wpos + 1) & 4095
                    mpos = (mpos + 1) & 4095
            bit <<= 1
    if len(out) != size:
        raise SetupError(f"{name}: decompressed to {len(out)} bytes, header declares {size}")
    return bytes(out)


# ---------------------------------------------------------------------------
# FAT12 floppy reader (read-only, root directory only)
# ---------------------------------------------------------------------------

def read_floppy_file(path, name):
    data = open(path, "rb").read()
    bps = struct.unpack_from("<H", data, 11)[0]
    spc = data[13]
    res = struct.unpack_from("<H", data, 14)[0]
    nf = data[16]
    rde = struct.unpack_from("<H", data, 17)[0]
    sfat = struct.unpack_from("<H", data, 22)[0]
    fat = data[res * bps:(res + nf * sfat) * bps]
    root_off = (res + nf * sfat) * bps
    data_off = root_off + rde * 32

    def next_cluster(c):
        j = (c * 3) // 2
        w = fat[j] | (fat[j + 1] << 8)
        return w >> 4 if c % 2 else w & 0xFFF

    def name11(e):
        stem = e[0:8].decode("cp437").rstrip()
        ext = e[8:11].decode("cp437").rstrip()
        return (stem + "." + ext) if ext else stem

    want = name.upper()
    for k in range(rde):
        e = data[root_off + k * 32:root_off + (k + 1) * 32]
        if e[0] == 0x00:
            break
        if e[0] == 0xE5 or e[11] == 0x0F or (e[11] & 0x18) == 0x08:
            continue
        if not (e[11] & 0x10) and name11(e).upper() == want:
            start = struct.unpack_from("<H", e, 26)[0]
            size = struct.unpack_from("<I", e, 28)[0]
            out = b""
            c = start
            while c < 0xFF8 and len(out) < size + spc * bps:
                off = data_off + (c - 2) * spc * bps
                out += data[off:off + spc * bps]
                c = next_cluster(c)
            return out[:size]
    raise SetupError(f"{name}: not found on {os.path.basename(path)}")


def expand_source(msclient_dir, floppy, stored):
    """Read a file from a source floppy and SZDD-expand it if compressed."""
    raw = read_floppy_file(os.path.join(msclient_dir, floppy), stored)
    if raw[:4] == b"SZDD":
        return szdd_decompress(raw, f"{floppy}:{stored}")
    return raw


# ---------------------------------------------------------------------------
# FAT16 disk image writer (partition aware, seek based — never loads the
# whole image into memory)
# ---------------------------------------------------------------------------

def _dos_now():
    now = datetime.now()
    date = ((now.year - 1980) << 9) | (now.month << 5) | now.day
    time_ = (now.hour << 11) | (now.minute << 5) | (now.second // 2)
    return date, time_


def _encode_name(name):
    stem, dot, ext = name.upper().rpartition(".")
    if not dot:
        stem, ext = name.upper(), ""
    if len(stem) > 8 or len(ext) > 3 or "." in stem:
        raise SetupError(f"bad 8.3 name: {name}")
    return stem.encode("ascii").ljust(8) + ext.encode("ascii").ljust(3)


def _decode_name(e):
    stem = e[0:8].decode("cp437").rstrip()
    ext = e[8:11].decode("cp437").rstrip()
    return (stem + "." + ext) if ext else stem


class FatVolume:
    def __init__(self, path, writable=False):
        self.path = path
        self.fh = open(path, "r+b" if writable else "rb")
        mbr = self._read(0, 512)
        self.base = 0
        for i in range(4):
            off = 0x1BE + i * 16
            if mbr[off + 4] in (0x01, 0x04, 0x06, 0x0E):
                lba = struct.unpack_from("<I", mbr, off + 8)[0]
                if lba:
                    self.base = lba * 512
                    break
        bpb = self._read(self.base, 512)
        self.bps = struct.unpack_from("<H", bpb, 11)[0]
        if self.bps != 512:
            raise SetupError(f"{path}: unsupported bytes/sector {self.bps}")
        self.spc = bpb[13]
        self.res = struct.unpack_from("<H", bpb, 14)[0]
        self.nf = bpb[16]
        self.rde = struct.unpack_from("<H", bpb, 17)[0]
        tot16 = struct.unpack_from("<H", bpb, 19)[0]
        self.sfat = struct.unpack_from("<H", bpb, 22)[0]
        tot32 = struct.unpack_from("<I", bpb, 32)[0]
        self.total = tot16 or tot32
        root_sectors = (self.rde * 32 + self.bps - 1) // self.bps
        self.root_off = self.base + (self.res + self.nf * self.sfat) * self.bps
        self.data_off = self.root_off + root_sectors * self.bps
        ncl = (self.total - self.res - self.nf * self.sfat - root_sectors) // self.spc
        if ncl < 4085:
            raise SetupError(f"{path}: not a FAT16 volume ({ncl} clusters)")
        self.max_cluster = ncl + 1
        self.fat_off = self.base + self.res * self.bps
        self.fat_len = self.sfat * self.bps
        raw_fat = self._read(self.fat_off, self.fat_len)
        self.fat = list(struct.unpack("<%dH" % (self.fat_len // 2), raw_fat))
        self._fat_dirty = False

    # -- low level ----------------------------------------------------------

    def _read(self, off, n):
        self.fh.seek(off)
        b = self.fh.read(n)
        if len(b) != n:
            raise SetupError(f"short read at {off}")
        return b

    def _write(self, off, data):
        self.fh.seek(off)
        self.fh.write(data)

    def _cluster_off(self, c):
        return self.data_off + (c - 2) * self.spc * self.bps

    def _chain(self, start):
        out = []
        c = start
        while 2 <= c < 0xFFF8:
            out.append(c)
            if len(out) > 65536:
                raise SetupError("cluster chain loops")
            c = self.fat[c]
        return out

    def _alloc(self, nbytes):
        need = max(1, (nbytes + self.spc * self.bps - 1) // (self.spc * self.bps))
        free = []
        for c in range(2, self.max_cluster + 1):
            if self.fat[c] == 0:
                free.append(c)
                if len(free) == need:
                    return free
        raise SetupError(f"out of free clusters (need {need})")

    def _set_chain(self, clusters):
        for i, c in enumerate(clusters):
            self.fat[c] = clusters[i + 1] if i + 1 < len(clusters) else 0xFFFF
        self._fat_dirty = True

    def _free_chain(self, start):
        for c in self._chain(start):
            self.fat[c] = 0
        self._fat_dirty = True

    def flush_fat(self):
        if not self._fat_dirty:
            return
        raw = struct.pack("<%dH" % len(self.fat), *self.fat)
        raw = raw[: self.fat_len].ljust(self.fat_len, b"\x00")
        for i in range(self.nf):
            self._write(self.fat_off + i * self.fat_len, raw)
        self._fat_dirty = False
        self.fh.flush()

    def close(self):
        self.flush_fat()
        self.fh.close()

    # -- directories ----------------------------------------------------------

    def _dir_raw(self, cluster):
        if cluster is None:
            return bytearray(self._read(self.root_off, self.rde * 32))
        raw = bytearray()
        for c in self._chain(cluster):
            raw += self._read(self._cluster_off(c), self.spc * self.bps)
        return raw

    def _put_dir_raw(self, cluster, raw):
        if cluster is None:
            if len(raw) != self.rde * 32:
                raise SetupError("root dir size changed")
            self._write(self.root_off, bytes(raw))
        else:
            chains = self._chain(cluster)
            cap = len(chains) * self.spc * self.bps
            if len(raw) > cap:
                raise SetupError("directory overflowed its cluster chain")
            for i, c in enumerate(chains):
                chunk = raw[i * self.spc * self.bps:(i + 1) * self.spc * self.bps]
                self._write(self._cluster_off(c), chunk)
        self.fh.flush()

    @staticmethod
    def _parse(raw):
        entries = []
        for i in range(len(raw) // 32):
            e = raw[i * 32:(i + 1) * 32]
            if e[0] == 0x00:
                break
            if e[0] == 0xE5 or e[11] == 0x0F or (e[11] & 0x08):
                continue
            start = struct.unpack_from("<H", e, 26)[0]
            size = struct.unpack_from("<I", e, 28)[0]
            entries.append((i, _decode_name(e), bool(e[11] & 0x10), start, size))
        return entries

    def _find(self, dir_cluster, name):
        for idx, nm, isdir, start, size in self._parse(self._dir_raw(dir_cluster)):
            if nm.upper() == name.upper():
                return idx, isdir, start, size
        return None

    def _parent(self, path):
        """Return (dir_cluster, leaf_name); dir_cluster None = root.

        Returns None if any intermediate directory is missing.
        """
        parts = [p for p in path.strip("/").split("/") if p]
        if not parts:
            raise SetupError(f"bad path {path!r}")
        cluster = None
        for part in parts[:-1]:
            hit = self._find(cluster, part)
            if hit is None or not hit[1]:
                return None
            cluster = hit[2]
        return cluster, parts[-1]

    # -- public API ------------------------------------------------------------

    def dos_installed(self):
        root = {nm for _i, nm, isdir, _s, _z in self._parse(self._dir_raw(None))}
        return {"IO.SYS", "MSDOS.SYS", "COMMAND.COM", "DOS"} <= root

    def exists(self, path):
        parent = self._parent(path)
        if parent is None:
            return False
        return self._find(*parent) is not None

    def read_file(self, path):
        parent = self._parent(path)
        if parent is None:
            return None
        hit = self._find(*parent)
        if hit is None or hit[1]:
            return None
        _idx, _d, start, size = hit
        out = b""
        for c in self._chain(start):
            out += self._read(self._cluster_off(c), self.spc * self.bps)
            if len(out) >= size:
                break
        return out[:size]

    def list_dir(self, path=""):
        if not path or path in ("/", "."):
            cluster = None
        else:
            parent = self._parent(path)
            if parent is None:
                return []
            hit = self._find(*parent)
            if hit is None or not hit[1]:
                return []
            cluster = hit[2]
        return [(nm, isdir, size) for _i, nm, isdir, _s, size in self._parse(self._dir_raw(cluster))]

    def mkdir(self, path):
        parts = [p for p in path.strip("/").split("/") if p]
        cluster = None
        for part in parts:
            hit = self._find(cluster, part)
            if hit is None:
                if cluster is not None:
                    raise SetupError("mkdir only supported directly under root here")
                new_cluster = self._alloc(self.spc * self.bps)[0]
                self._set_chain([new_cluster])
                raw = self._dir_raw(None)
                slot = self._free_slot(raw)
                date, time_ = _dos_now()
                e = bytearray(32)
                e[0:11] = _encode_name(part)
                e[11] = 0x10
                struct.pack_into("<H", e, 22, time_)
                struct.pack_into("<H", e, 24, date)
                struct.pack_into("<H", e, 26, new_cluster)
                raw[slot * 32:(slot + 1) * 32] = e
                self._put_dir_raw(None, raw)
                # blank directory content (root slot chain is set; write zeros)
                self._write(self._cluster_off(new_cluster), b"\x00" * (self.spc * self.bps))
                cluster = new_cluster
            elif not hit[1]:
                raise SetupError(f"{part} exists but is not a directory")
            else:
                cluster = hit[2]

    @staticmethod
    def _free_slot(raw):
        for i in range(len(raw) // 32):
            b0 = raw[i * 32]
            if b0 in (0x00, 0xE5):
                return i
        raise SetupError("directory full")

    def put_file(self, path, data):
        parent = self._parent(path)
        if parent is None:
            raise SetupError(f"put_file: parent directory missing for {path}")
        dir_cluster, leaf = parent
        raw = self._dir_raw(dir_cluster)
        hit = self._find(dir_cluster, leaf)
        if hit:
            idx = hit[0]
            old_start = hit[2]
        else:
            idx = self._free_slot(raw)
            old_start = None
        clusters = self._alloc(len(data))
        self._set_chain(clusters)
        if old_start:
            self._free_chain(old_start)
        cap = self.spc * self.bps
        for i, c in enumerate(clusters):
            chunk = data[i * cap:(i + 1) * cap]
            chunk = chunk.ljust(cap, b"\x00")
            self._write(self._cluster_off(c), chunk)
        date, time_ = _dos_now()
        e = bytearray(32)
        e[0:11] = _encode_name(leaf)
        e[11] = 0x20
        struct.pack_into("<H", e, 22, time_)
        struct.pack_into("<H", e, 24, date)
        struct.pack_into("<H", e, 26, clusters[0])
        struct.pack_into("<I", e, 28, len(data))
        raw[idx * 32:(idx + 1) * 32] = e
        self._put_dir_raw(dir_cluster, raw)
        self.flush_fat()
        return len(data)


# ---------------------------------------------------------------------------
# Configuration content
# ---------------------------------------------------------------------------

def build_configs(smb_name):
    autoexec = (
        "C:\\DOS\\SMARTDRV.EXE /X\r\n"
        "@ECHO OFF\r\n"
        "PROMPT $p$g\r\n"
        "PATH C:\\DOS;C:\\NET\r\n"
        "SET TEMP=C:\\DOS\r\n"
        "net initialize\r\n"
        "netbind.com\r\n"
        "umb.com\r\n"
        "tcptsr.exe\r\n"
        "tinyrfc.exe\r\n"
        "nmtsr.exe\r\n"
        "emsbfr.exe\r\n"
        "net start\r\n"
        "call c:\\net\\netmap.bat\r\n"
    )
    config_sys = (
        "DEVICE=C:\\DOS\\SETVER.EXE\r\n"
        "DEVICE=C:\\DOS\\HIMEM.SYS\r\n"
        "DEVICE=C:\\DOS\\EMM386.EXE RAM\r\n"
        "DOS=HIGH,UMB\r\n"
        "FILES=30\r\n"
        "device=C:\\NET\\ifshlp.sys\r\n"
        "LASTDRIVE=Z\r\n"
    )
    system_ini = (
        "[network]\r\n"
        "sizworkbuf=1498\r\n"
        "filesharing=no\r\n"
        "printsharing=no\r\n"
        "autologon=yes\r\n"
        f"computername={COMPUTERNAME}\r\n"
        "lanroot=C:\\NET\r\n"
        f"username={USERNAME}\r\n"
        f"workgroup={WORKGROUP}\r\n"
        "reconnect=yes\r\n"
        "directhost=no\r\n"
        "dospophotkey=N\r\n"
        "lmlogon=0\r\n"
        f"logondomain={WORKGROUP}\r\n"
        "preferredredir=full\r\n"
        "autostart=full\r\n"
        "maxconnections=8\r\n"
        "\r\n"
        "[Password Lists]\r\n"
        "DOS622=C:\\NET\\DOS622.PWL\r\n"
        "*Shares=C:\\NET\\SHARES.PWL\r\n"
        "\r\n"
        "[network drivers]\r\n"
        "netcard=pcind.dos\r\n"
        "transport=tcpdrv.dos,nemm.dos\r\n"
        "devdir=C:\\NET\r\n"
        "LoadRMDrivers=yes\r\n"
        "pcind.dos=LOW\r\n"
        "\r\n"
        "[386enh]\r\n"
        "TimerCriticalSection=5000\r\n"
        "UniqueDosPSP=TRUE\r\n"
        "PSPIncrement=2\r\n"
    )
    ip, mask, gw = GUEST_IP.replace(".", " "), GUEST_NETMASK.replace(".", " "), GATEWAY.replace(".", " ")
    protocol_ini = (
        "[network.setup]\r\n"
        "version=0x3110\r\n"
        "netcard=RTL8029,1,RTL8029,1\r\n"
        "transport=tcpip,TCPIP\r\n"
        "lana0=RTL8029,1,tcpip\r\n"
        "\r\n"
        "[protman]\r\n"
        "DriverName=PROTMAN$\r\n"
        "PRIORITY=RTL8029\r\n"
        "\r\n"
        "[TCPIP]\r\n"
        "DriverName=TCPIP$\r\n"
        "BINDINGS=RTL8029\r\n"
        "LANABASE=0\r\n"
        "NBSessions=6\r\n"
        "DisableDHCP=1\r\n"
        f"IPAddress0={ip}\r\n"
        f"SubNetMask0={mask}\r\n"
        f"DefaultGateway0={gw}\r\n"
        "\r\n"
        "[RTL8029]\r\n"
        "DriverName=PCIND$\r\n"
    )
    lmhosts = f"{GATEWAY} {LMHOSTS_NAME}  #PRE\r\n"
    netmap = (
        "@echo off\r\n"
        "net use f: /delete >nul\r\n"
        f"net use f: \\\\{LMHOSTS_NAME}\\{smb_name} /persistent:no\r\n"
    )
    return {
        "AUTOEXEC.BAT": autoexec.encode("cp437"),
        "CONFIG.SYS": config_sys.encode("cp437"),
        "NET/SYSTEM.INI": system_ini.encode("cp437"),
        "NET/PROTOCOL.INI": protocol_ini.encode("cp437"),
        "NET/LMHOSTS": lmhosts.encode("cp437"),
        "NET/NETMAP.BAT": netmap.encode("cp437"),
        "NET/WFWSYS.CFG": base64.b64decode(WFWSYS_CFG_B64),
        "NET/SHARES.PWL": base64.b64decode(SHARES_PWL_B64),
        "NET/DOS622.PWL": base64.b64decode(DOS622_PWL_B64),
    }


# ---------------------------------------------------------------------------
# Planning / install / verify
# ---------------------------------------------------------------------------

def load_package(system):
    path = os.path.join(SYSTEMS_DIR, system, "package.yaml")
    if not os.path.isfile(path):
        raise SetupError(f"no package.yaml for system '{system}'")
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def qmp_listening(pkg):
    port = int(pkg.get("qemu", {}).get("qmp_port") or 0)
    if not port:
        return False
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.3):
            return True
    except OSError:
        return False


def build_contents(system_dir, smb_name):
    """Return {guest_path: bytes} for everything the install writes."""
    msclient = os.path.join(system_dir, "msclient")
    contents = {}
    for floppy, stored, dest in MANIFEST:
        data = expand_source(msclient, floppy, stored)
        guest = dest if "/" in dest else "NET/" + dest
        contents[guest] = data
    contents.update(build_configs(smb_name))
    return contents


def find_c_image(system_dir, pkg):
    for drive in pkg.get("qemu", {}).get("drives", []):
        if drive.get("interface") == "ide":
            return os.path.join(system_dir, drive["file"])
    return os.path.join(system_dir, "dos622_c.img")


def install(system_dir, pkg, contents, force=False):
    image = find_c_image(system_dir, pkg)
    if not os.path.isfile(image):
        raise SetupError(f"disk image not found: {image} (run install.py first)")
    vol = FatVolume(image, writable=True)
    try:
        if not vol.dos_installed():
            raise SetupError("DOS is not installed in the image yet "
                             "(boot the DOS 6.22 setup floppies first)")
        if vol.exists("NET/PROTOCOL.INI"):
            if not force:
                raise SetupError("network stack already installed (use --force to redo)")
        if not vol.exists("NET"):
            vol.mkdir("NET")
        for guest, data in sorted(contents.items()):
            old = vol.read_file(guest)
            verb = "replace" if old is not None else "write"
            vol.put_file(guest, data)
            print(f"  {verb:<7} {guest:<22} {len(data):>7} bytes")
    finally:
        vol.close()
    return image


def verify(image, contents):
    vol = FatVolume(image, writable=False)
    try:
        if not vol.dos_installed():
            raise SetupError("verify: DOS files vanished?!")
        net = {nm for nm, isdir, _sz in vol.list_dir("NET") if not isdir}
        expected_net = {p.split("/", 1)[1] for p in contents if p.startswith("NET/")}
        missing = expected_net - net
        if missing:
            raise SetupError(f"verify: missing from C:\\NET: {', '.join(sorted(missing))}")
        for guest, want in sorted(contents.items()):
            got = vol.read_file(guest)
            if got != want:
                raise SetupError(f"verify: read-back mismatch for {guest} "
                                 f"({0 if got is None else len(got)} vs {len(want)} bytes)")
        return len(expected_net)
    finally:
        vol.close()


# ---------------------------------------------------------------------------
# --test: headless boot acceptance
# ---------------------------------------------------------------------------

QCODES = {" ": ["spc"], "\\": ["backslash"], ".": ["dot"], ",": ["comma"],
          ";": ["semicolon"], ":": ["shift", "semicolon"], ">": ["shift", "dot"],
          "_": ["shift", "minus"], "-": ["minus"], "=": ["equal"], "/": ["slash"]}


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


def send_text(qmp, text):
    for ch in text:
        if ch.isalpha():
            keys = ["shift", ch.lower()] if ch.isupper() else [ch]
        elif ch.isdigit():
            keys = [ch]
        elif ch in QCODES:
            keys = QCODES[ch]
        else:
            raise SetupError(f"no key mapping for {ch!r}")
        qmp.send({"execute": "send-key",
                  "arguments": {"keys": [{"type": "qcode", "data": k} for k in keys]}})
        time.sleep(0.05)


def wait_qmp(port, deadline_s=60):
    end = time.monotonic() + deadline_s
    while time.monotonic() < end:
        try:
            return Qmp("127.0.0.1", port)
        except OSError:
            time.sleep(0.5)
    raise SetupError("QMP never came up")


def type_probe(qmp, probe):
    print(f"  typing: {probe}")
    send_text(qmp, probe)
    qmp.send({"execute": "send-key",
              "arguments": {"keys": [{"type": "qcode", "data": "ret"}]}})


def run_test(system, pkg, contents):
    system_dir = os.path.join(SYSTEMS_DIR, system)
    if qmp_listening(pkg):
        raise SetupError("VM already running (QMP port busy); stop it first")

    net = pkg.get("network", {})
    share = os.path.abspath(os.path.join(system_dir, net.get("smb_dir", "share")))
    os.makedirs(share, exist_ok=True)
    port = int(net.get("smb_port", 139))
    smb_name = net.get("smb_name", "HOST")
    log_path = os.path.join(system_dir, "smb_host.log")
    target = os.path.join(share, "zz.txt")
    if os.path.exists(target):
        os.remove(target)

    # boot from the HDD only: package.yaml's DOS Setup floppy would win order=ac
    test_pkg = yaml.safe_load(yaml.dump(pkg))
    q = test_pkg.setdefault("qemu", {})
    q["drives"] = [d for d in q.get("drives", []) if d.get("interface") != "floppy"]
    q["boot_order"] = "c"

    smb_cmd = [sys.executable, os.path.join(ROOT, "smb_host.py"),
               "--dir", share, "--name", smb_name, "--port", str(port),
               "--log", log_path]
    smb_proc = subprocess.Popen(smb_cmd, cwd=ROOT, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL)
    qemu_proc = None
    qmp = None
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if smb_proc.poll() is not None:
                raise SetupError(f"smb_host exited ({smb_proc.returncode}); see {log_path}")
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                    break
            except OSError:
                time.sleep(0.1)
        else:
            raise SetupError(f"smb_host not listening on {port}; see {log_path}")

        sys.path.insert(0, ROOT)
        import start as start_mod
        cmd = start_mod.build_command(system, test_pkg) + ["-display", "none"]
        print("  launching: " + " ".join(cmd))
        qemu_proc = subprocess.Popen(cmd, cwd=ROOT)
        qmp_port = int(pkg["qemu"]["qmp_port"])
        qmp = wait_qmp(qmp_port)
        print("  waiting for DOS boot ...")
        # wait for the guest's NETMAP to map F: (TREE_CONNECT hits smb_host),
        # then give AUTOEXEC a moment to return to the DOS prompt
        log_mark = os.path.getsize(log_path) if os.path.exists(log_path) else 0
        deadline = time.monotonic() + 90
        connected = False
        while time.monotonic() < deadline:
            try:
                with open(log_path, "r", encoding="utf-8", errors="replace") as fh:
                    fh.seek(log_mark)
                    if "TREE_CONNECT" in fh.read():
                        connected = True
                        break
            except OSError:
                pass
            time.sleep(1)
        print(f"  guest mapped F: {'yes' if connected else 'NO'}")
        time.sleep(4)

        probe = "copy c:\\config.sys f:\\zz.txt"
        type_probe(qmp, probe)
        time.sleep(10)

        want = contents["CONFIG.SYS"]
        ok = False
        detail = ""
        if os.path.isfile(target):
            got = open(target, "rb").read()
            if got == want:
                ok = True
            else:
                detail = f"share/zz.txt content mismatch ({len(got)} vs {len(want)} bytes)"
        else:
            # boot may have finished late (network init is slow) — probe once more
            print("  not created yet; retrying probe ...")
            time.sleep(15)
            type_probe(qmp, probe)
            time.sleep(10)
            if os.path.isfile(target):
                got = open(target, "rb").read()
                if got == want:
                    ok = True
                else:
                    detail = f"share/zz.txt content mismatch ({len(got)} vs {len(want)} bytes)"
            else:
                detail = "share/zz.txt was never created (F: write failed or boot hung)"
        if not ok:
            ppm = os.path.join(system_dir, "test_fail.ppm")
            qmp.send({"execute": "screendump", "arguments": {"filename": ppm}})
            print(f"  screendump saved: {ppm}")
        else:
            try:
                os.remove(target)
            except OSError:
                pass
        return ok, detail
    finally:
        if qmp is not None:
            try:
                qmp.send({"execute": "quit"})
            except OSError:
                pass
            try:
                qmp.close()
            except OSError:
                pass
        if qemu_proc is not None:
            try:
                qemu_proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                qemu_proc.kill()
                qemu_proc.wait()
        if smb_proc.poll() is None:
            smb_proc.terminate()
            try:
                smb_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                smb_proc.kill()
                smb_proc.wait()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Install the MS Network Client stack into a DOS disk image (offline).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__)
    parser.add_argument("system", help="system name (e.g. dos622)")
    parser.add_argument("--check", action="store_true",
                        help="report installed/not; exit 0 if installed")
    parser.add_argument("--dry-run", action="store_true",
                        help="expand and plan everything, touch nothing")
    parser.add_argument("--force", action="store_true",
                        help="overwrite an existing network stack")
    parser.add_argument("--test", action="store_true",
                        help="after install: boot headless and verify F: write end-to-end")
    args = parser.parse_args()

    system_dir = os.path.join(SYSTEMS_DIR, args.system)
    pkg = load_package(args.system)
    smb_name = pkg.get("network", {}).get("smb_name", "HOST")
    image = os.path.join(system_dir, "dos622_c.img")

    try:
        if not os.path.isfile(image):
            raise SetupError(f"disk image not found: {image} (run install.py first)")

        if args.check:
            vol = FatVolume(image, writable=False)
            try:
                dos_ok = vol.dos_installed()
                net_ok = vol.exists("NET/PROTOCOL.INI")
            finally:
                vol.close()
            print(f"  DOS installed:      {'OK' if dos_ok else 'MISSING'}")
            print(f"  network stack:      {'OK' if net_ok else 'MISSING'}")
            sys.exit(0 if (dos_ok and net_ok) else 1)

        print(f"Expanding MS Network Client sources for {args.system} ...")
        contents = build_contents(system_dir, smb_name)
        print(f"  {len(contents)} files planned "
              f"({sum(len(v) for v in contents.values())} bytes total)")

        if args.dry_run:
            for guest, data in sorted(contents.items()):
                print(f"    {guest:<24} {len(data):>7} bytes")
            vol = FatVolume(image, writable=False)
            try:
                print(f"  DOS installed:      {'OK' if vol.dos_installed() else 'MISSING'}")
                print(f"  network installed:  {'yes (would need --force)' if vol.exists('NET/PROTOCOL.INI') else 'no'}")
            finally:
                vol.close()
            print("Dry run — nothing written.")
            return

        if qmp_listening(pkg):
            raise SetupError("VM appears to be running (QMP port is open); "
                             "shut it down first — the image must not be in use")

        vol = FatVolume(image, writable=False)
        try:
            installed = vol.exists("NET/PROTOCOL.INI")
        finally:
            vol.close()
        if installed and not args.force:
            print("Network stack already installed — skipping write "
                  "(use --force to redo).")
        else:
            print("Writing network stack into the disk image ...")
            install(system_dir, pkg, contents, force=args.force)
        print("Verifying read-back ...")
        count = verify(image, contents)
        print(f"  OK — {count} files in C:\\NET verified byte-for-byte.")

        if args.test:
            print("Running headless boot acceptance test ...")
            ok, detail = run_test(args.system, pkg, contents)
            if not ok:
                raise SetupError(f"boot test FAILED: {detail}")
            print("  OK — guest wrote C:\\CONFIG.SYS to F:; host file matches image.")

        print("\nNetwork stack installed. Next `start.py` boot maps F: automatically.")
    except SetupError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
