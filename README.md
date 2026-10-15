# classic-systems

Run MS-DOS 6.22 in QEMU with a **live two-way SMB1 share** — the host directory
`systems/dos622/share/` appears as the guest's `F:` drive. Drop files in from
Windows; they show up in DOS instantly, and vice versa.

## Prerequisites

- Windows 10 / 11, **Python 3.11+**
- ~2.5 GB free for QEMU (~250 MB) + 2 GB disk image + network client (~3 MB)
- `PyYAML` — install once: `python -m pip install pyyaml`
- Internet connection on first run (installs download QEMU + network stack)

## Quick start

```
python start.py dos622      # auto-installs missing deps, then boots
```

On first run this downloads ~250 MB of QEMU binaries plus ~3 MB of DOS
networking files. Subsequent runs skip the downloads.

For explicit control, run the installer separately:

```
python install.py           # downloads everything (use --check to preview)
python start.py dos622      # boots the VM
```

The QEMU window opens. The DOS boot takes ~30 s (PCnet driver → TCP/IP
stack → login → share mapping). At the `C:\>` prompt, `F:` is already
mapped to `systems/dos622/share/` on the host.

```
F: connected to \\SMBSERVER\HOST.
The command completed successfully.

C:\>dir f:

 Volume in drive F has no label
 Directory of F:\

HELLO     TXT         106 10-13-26 10:01p
              1 file(s)            106 bytes
      266,240,000 bytes free
```

## Using the share

### Inside the guest

```
copy c:\autoexec.bat f:\BACKUP.BAT
copy f:\HOSTFILE.TXT c:\HOSTFILE.TXT
dir f:
type f:\HELLO.TXT
```

### On the host

Files you drop into `systems/dos622/share/` (via Explorer, `copy`, etc.)
appear on `F:` the next time DOS reads the directory. Files created by the
guest appear on the host immediately (the server flushes writes).

## Hot-swapping floppies

The repo ships with the four MS-DOS 6.22 setup floppies and the MS Network
Client 3.0b disks. Insert and eject at runtime:

```
python floppy.py dos622 insert msclient/Disk1.img   # into drive A:
python floppy.py dos622 eject                       # eject drive A:
python floppy.py dos622 query                       # dump block devices (debug)
```

The floppy device is always present in the machine; it just starts empty when
no floppy drive is configured. SeaBIOS falls through to the HDD in that case.

## Adding a new system

1. Create `systems/<name>/package.yaml` — copy the `dos622` one and adjust.
2. Drop any floppy images into `systems/<name>/`.
3. Run `python start.py <name>`.

Run `python start.py` with no argument to list available systems.

## What install.py does

| Dependency | Source | Size | Auto-download |
|------------|--------|------|---------------|
| QEMU w64 binaries | qemu.weilnetz.de/w64/ | ~250 MB | Yes |
| MS Network Client 3.0b (Disk1/2) | archive.org/msnc3 | ~3 MB | Yes |
| RTL8029 NDIS2 driver floppy | archive.org/pcind.dos + build | 1.44 MB | Yes |
| Blank 2 GB C: drive | `qemu-img create` | 2 GB | Yes |
| `share/HELLO.TXT` | created locally | 106 B | Yes |
| `systems/dos622/package.yaml` | repo file | — | N/A |
| `floppy.py`, `smb_host.py`, `start.py` | repo files | — | N/A |
| DOS 6.22 setup floppies (`dos622_1..4.img`) | **user-provided** | ~6 MB | No |

The DOS 6.22 setup floppies are copyrighted retail media and cannot be
auto-downloaded. Place them manually in `systems/dos622/` or obtain them from
your own disks or a legally-acquired ISO image. Boot works without them
(falls through to the hard disk).

## File layout

```
.
├── install.py          # downloads QEMU + MSN + RTL8029, creates C: drive + share/
├── start.py            # launches SMB server + QEMU (auto-calls install.ensure_all)
├── floppy.py           # runtime floppy insert/eject via QMP
── smb_host.py         # stdlib-only SMB1 server (host-side)
├── deps/qemu/AMD64/    # QEMU binaries (created by install.py)
└── systems/dos622/
    ├── package.yaml
    ├── dos622_{1..4}.img       # MS-DOS 6.22 setup floppies (user-provided)
    ├── dos622_c.img            # 2 GB FAT16 C: drive (auto-created)
    ├── smb_host.log            # server request log (append-only)
    ├── msclient/               # network client disks
    │   ├── Disk1.img, Disk2.img    # MS Network Client 3.0b (auto-downloaded)
    │   ├── pcind.img               # RTL8029 NDIS2 driver (auto-built)
    │   ├── swapnet.img             # optional recovery floppy (manual)
    │   └── config.img              # optional recovery floppy (manual)
    └── share/                      # ← host folder, exposed as F: (auto-created)
        └── HELLO.TXT
```

## CLI flags

```
python install.py --check      # report missing deps without installing
python install.py --qemu-only  # legacy: only install QEMU
python install.py --system-dir systems/dos622  # target a different system

python start.py --skip-deps dos622   # bypass dep check (e.g. air-gapped)
```

## Notes & gotchas

- **First run is slow** — QEMU download is ~250 MB. Subsequent runs are fast.
- **Don't touch `smb_host.py`'s port** — the DOS redirector connects to
  port 139 by default. QEMU's slirp NAT makes `10.0.2.2:139` in the guest
  reach `127.0.0.1:139` on the host.
- **Don't change the share name in LMHOSTS** — `10.0.2.2 smbserver  #PRE`
  must match `smb_name` in `package.yaml` (`HOST` by default). Case-insensitive
  but text-identical.
- **Rebooting is fast** — the server restarts automatically; F: re-maps in ~10 s.
- **The share is SMB1/LANMAN — plaintext, no auth.** Fine for a local VM; do
  not expose port 139 to a real network.
- **Windows Defender is quiet** — `smb_host.py` is stdlib-only (no `impacket`);
  there's nothing to quarantine.
- **The C: image is raw FAT16** — mount it on the host with OSFMount or
  `diskpart attach vdisk` (VM must be off) for offline editing.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| First run hangs for 30+ s | QEMU is downloading (~250 MB) | Wait; watch progress on stderr |
| `install.py --check` shows MISSING | Some dep not downloaded | Run `python install.py` |
| QEMU window flashes closed | QMP port 4444 already in use | Kill lingering `qemu-system-x86_64.exe`; or pick a different `qmp_port` |
| `dir f:` → "Invalid drive specification" | SMB server process died | Check `systems/dos622/smb_host.log` for tracebacks |
| `Error 53` computer name not found | LMHOSTS missing/wrong | Re-apply `10.0.2.2 smbserver  #PRE\r\n` to `C:\NET\LMHOSTS` in the image |
| `Error 66` network resource type incorrect | Stale `smb_host.py` | Update to the current version (uses `Service="A:"` for disk shares) |
| Boot hangs at "Loading PCI Ethernet..." | EMM386 / UMB missing, or `LH` on TSRs | `DOS=HIGH,UMB` in CONFIG.SYS; remove `LH` prefixes from AUTOEXEC |
| "DOS 6.22 floppies missing" warning | User-provided media absent | Place `dos622_1..4.img` in `systems/dos622/`, or ignore (boots from HDD) |

## Credits

- QEMU Windows builds: https://qemu.weilnetz.de/w64/ (Stefan Weil)
- MS Network Client 3.0b: https://archive.org/details/msnc3 (Microsoft, 1994)
- RTL8029 NDIS2 driver: https://archive.org/details/pcind.dos (Realtek, ~2000)
- MS-DOS 6.22 setup disks: 4 × 1.44 MB images from a 1994 retail set
- SMB1 wire format: Microsoft MS-CIFS spec [MS-CIFS]-250210
