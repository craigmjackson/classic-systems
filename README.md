# classic-systems

Run MS-DOS 6.22 in QEMU with a **live two-way SMB1 share** — the host directory
`systems/dos622/share/` appears as the guest's `F:` drive. Drop files in from
Windows; they show up in DOS instantly, and vice versa.

## Prerequisites

- Windows 10 / 11, **Python 3.11+**
- **MS-DOS 6.22 setup floppies — user-provided.** They are copyrighted
  retail media and are **not** included with this project or auto-downloaded;
  obtain them from your own disks or a legally-acquired ISO (see
  "What install.py does")
- ~4 GB free for QEMU (~1.2 GB extracted) + 2 GB disk image + Open Watcom
  toolchain (~500 MB) + network client (~3 MB)
- `PyYAML` — install once: `python -m pip install pyyaml`
- Internet connection on first run (installs download QEMU + network stack +
  the Open Watcom/PDCurses toolchain used to build the in-guest menu)

## Quick start

```
python start.py dos622      # auto-installs missing deps, then boots
```

On first run this downloads ~250 MB of QEMU binaries plus ~3 MB of DOS
networking files, and fetches the Open Watcom + PDCurses toolchain that
builds the in-guest menu launcher. Subsequent runs skip the downloads.

What it does **not** download is MS-DOS itself. On a fresh checkout there is
no bootable disk yet, so you must provide the DOS 6.22 setup floppies
yourself: place `dos622_1.img`–`dos622_4.img` in `systems/dos622/`, let
DOS Setup run once from drive A:, then install networking with
`python setup_net.py dos622` (see "Installing the network stack"). An
already-installed `dos622_c.img` needs no floppies and boots straight to
`C:\>` with `F:` mapped.

For explicit control, run the installer separately:

```
python install.py           # downloads all auto-installable deps (use --check to preview)
python start.py dos622      # boots the VM
```

The QEMU window opens. The DOS boot takes ~30 s (PCnet driver → TCP/IP
stack → login → share mapping). At the `C:\>` prompt, `F:` is already
mapped to `systems/dos622/share/` on the host. For a point-and-shoot
launcher instead of the command line, run `f:\menu\menu.exe` (see
"The MENU launcher").

```
F: connected to \\SMBSERVER\HOST.
The command completed successfully.

C:\>dir f:

 Volume in drive F has no label
 Directory of F:\

HELLO    TXT           108 10-17-26  12:47p
APPS         <DIR>         10-18-26   3:18p
GAMES        <DIR>         10-18-26   9:10p
MENU         <DIR>         10-18-26   9:00p
        4 file(s)            108 bytes
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

## The MENU launcher

`F:\MENU\MENU.EXE` is an in-guest TUI (PDCurses) for browsing and launching
your DOS games and apps. It is built automatically from the `tools/menu/`
sources on every `start.py` run (only when the sources are newer than the
binary); force a rebuild with `python install.py --build-tools`.

```
C:\>f:\menu\menu.exe
```

The main screen shows **Games**, **Apps** and **Quit**, with a live package
count next to each. Games and Apps list one entry per subdirectory of
`F:\GAMES\` / `F:\APPS\` that contains a `PACKAGE.INI`.

| Key | Action |
|-----|--------|
| ↑ / ↓, Enter | move selection / run the highlighted package |
| Esc | back to the previous screen; from the main screen, quit |
| `e` | edit the selected package (name / executable / args) |
| `Ins` or `a` | add a package — asks for directory, name, executable, args |
| `Del` | delete the selected package: `y` removes only `PACKAGE.INI`; `d` starts a full delete (package directory included) and then requires you to type `DELETE` to confirm |

### PACKAGE.INI

Each package is a directory on the share holding the program's files plus a
small metadata file:

```ini
[package]
name=Hello Game
executable=hello.bat
args=gamer
```

- `name` — display name in the list
- `executable` — file inside the package directory; `.com` and `.exe` are
  executed directly, everything else (including `.bat`) runs through
  `COMMAND.COM /c`
- `args` — optional command line appended when the package runs

To add a game by hand: copy its files into `F:\GAMES\MYGAME\` and drop in a
`PACKAGE.INI` as above — it appears the next time the list opens. Two demo
packages ship with the install (`F:\GAMES\HELLO` and `F:\APPS\HELLO`);
restore them at any time with `python install.py --reseed-hello`.

## Installing DOS & hot-swapping floppies

`package.yaml` points drive A: at `dos622_1.img` and sets `boot_order: ac`,
so a fresh install boots the DOS 6.22 Setup floppy first. The 4-floppy set
is user-provided copyrighted media — see "What install.py does"; if it is
absent, `start.py` warns and skips the missing floppy so the boot falls
through to the hard disk. After setup finishes:

```
python floppy.py dos622 eject                       # eject drive A:
```

This ejects the disk and strips the floppy entry from `package.yaml`, so the
next `start.py` launch boots straight from the hard disk.

Both `eject` and `insert` work when no VM is running — they just edit
`package.yaml` directly (there's no live media to change). `insert` records
the image so the next boot has it in drive A:. Only `query` requires a
running VM. Re-insert any image
at runtime:

```
python floppy.py dos622 insert msclient/Disk1.img   # into drive A:
python floppy.py dos622 query                       # dump block devices (debug)
```

## Installing the network stack (no guest SETUP needed)

`setup_net.py` installs the complete MS Network Client stack **offline**,
directly into `dos622_c.img` — no in-guest SETUP run, no unattended-answer
file. It expands the install sources from `systems/dos622/msclient/`, writes
36 files (C:\NET binaries, SYSTEM.INI, PROTOCOL.INI with static IP + RTL8029,
AUTOEXEC/NETMAP boot lines, LMHOSTS/HOSTS, embedded WFWSYS.CFG + SHARES.PWL +
DOS622.PWL), and cross-checks every file byte-for-byte after writing.

```
python setup_net.py dos622              # install (VM must be off)
python setup_net.py dos622 --check      # report installed/not (exit 0/1)
python setup_net.py dos622 --dry-run    # expand + plan, touch nothing
python setup_net.py dos622 --force      # overwrite an existing stack
python setup_net.py dos622 --test       # boot headless + verify F: end-to-end
```

`--test` boots the image with `-display none`, waits for the guest's
NETMAP to map `F:` (detected via `smb_host.log`), then runs
`copy c:\config.sys f:\zz.txt` in the guest and verifies the host file
matches the image byte-for-byte. On success, `start.py dos622` boots
straight to a `C:\>` prompt with `F:` mapped — logon is silent (the image
ships a pre-made `DOS622.PWL`).

## Adding a new system

1. Create `systems/<name>/package.yaml` — copy the `dos622` one and adjust.
2. Drop any floppy images into `systems/<name>/`.
3. Run `python start.py <name>`.

Run `python start.py` with no argument to list available systems.

## package.yaml schema

Each system is described by a `systems/<name>/package.yaml` file. `start.py`
reads it to build the QEMU command line; `install.py` uses it to find the
system directory and seed the share.

```yaml
name: dos622                   # system identifier (matches directory name)
description: MS-DOS 6.22       # human-readable label

qemu:
  binary: qemu-system-x86_64.exe   # QEMU binary in deps/qemu/AMD64/
  machine: pc                     # QEMU -machine value
  cpus: 1                         # -smp value (MS-DOS: use 1)
  memory_mb: 64                   # -m value (MS-DOS max ~64 MB via HIMEM)
  boot_order: ac                  # SeaBIOS -boot order=... ('ac' = A: then C:)
                                  # 'a'/'b' are stripped automatically when no
                                  # floppy drive is configured
  qmp_port: 4444                  # TCP port for QMP (floppy.py, automation)
  drives:                         # ordered list; first floppy entry = boot disk
    - interface: floppy           #   -fda/-fdb <file>
      device: fda                 #   fda (drive A:) or fdb (drive B:)
      file: dos622_1.img          #   relative to systems/<name>/
    - interface: ide              #   -drive if=ide,index=N,media=disk,file=<file>
      file: dos622_c.img          #   auto-created by install.py if missing
      size: 2048M                 #   raw disk size (qemu-img create)
  args: []                        # extra args appended verbatim (e.g. -display none)

network:
  nic: ne2k_pci                   # QEMU NIC device name (ms$ne2clone on ne2k_isa
                                  # truncates TX at 60 bytes — use ne2k_pci +
                                  # PCIND.DOS)
  smb_dir: share                  # host folder relative to systems/<name>/
  smb_name: HOST                  # share name on the wire (\\SMBSERVER\HOST)
  smb_port: 139                   # host TCP port the SMB server binds (loopback)
```

### Field reference

| Field | Required | Default | Description |
|-------|----------|---------|-------------|
| `name` | yes | — | System identifier; must match the directory name |
| `description` | no | — | Human-readable label |
| `qemu.binary` | no | `qemu-system-x86_64.exe` | QEMU executable in `deps/qemu/AMD64/` |
| `qemu.machine` | no | — | `-machine` value |
| `qemu.cpus` | no | — | `-smp` value |
| `qemu.memory_mb` | no | — | `-m` value (guest RAM in MB) |
| `qemu.boot_order` | no | — | SeaBIOS `-boot order=` string; 'a'/'b' auto-stripped when no floppy drive present |
| `qemu.qmp_port` | no | — | TCP port for QMP (used by `floppy.py`) |
| `qemu.drives` | yes | — | Ordered drive list. First floppy entry = boot floppy. IDE disks auto-created via `qemu-img` |
| `qemu.drives[].interface` | yes | — | `floppy` or `ide` |
| `qemu.drives[].file` | yes | — | image filename, relative to `systems/<name>/` |
| `qemu.drives[].device` | no | `fda` | floppy device name (`fda` or `fdb`) |
| `qemu.drives[].size` | no | `2048M` | raw disk size (only meaningful for `ide`) |
| `qemu.args` | no | `[]` | extra args appended verbatim to the QEMU command |
| `network.nic` | no | — | QEMU NIC device name (e.g. `ne2k_pci`) |
| `network.smb_dir` | no | — | host folder to expose (relative to `systems/<name>/`) |
| `network.smb_name` | no | `HOST` | share name on the wire |
| `network.smb_port` | no | `139` | host TCP port for the SMB server (loopback) |

### Notes

- If no floppy drive is listed in `drives`, `start.py` still attaches an empty
  `-fda ""` so `floppy.py insert` works at runtime.
- `qmp_port` and `smb_port` must not collide with other running instances.
- DOS 6.22 limits: 1 CPU, 64 MB RAM (HIMEM.SYS max), 2 GB disk (FAT16/FDISK max).

## What install.py does

| Dependency | Source | Size | Auto-download |
|------------|--------|------|---------------|
| QEMU w64 binaries | qemu.weilnetz.de/w64/ | ~250 MB (~1.2 GB extracted) | Yes |
| MS Network Client 3.0b (Disk1/2) | archive.org/msnc3 | ~3 MB | Yes |
| RTL8029 NDIS2 driver floppy | archive.org/pcind.dos + build | 1.44 MB | Yes |
| Open Watcom 2.0 (C toolchain) | github.com/open-watcom releases | ~500 MB extracted | Yes |
| PDCurses 3.9 (curses library) | github.com/wmcbrine/PDCurses | ~2 MB | Yes (built with Watcom) |
| Blank 2 GB C: drive | `qemu-img create` | 2 GB | Yes |
| `share/HELLO.TXT` | created locally | 108 B | Yes |
| `share/menu/MENU.EXE`, `share/apps/HELLO/HELLO.EXE` | built from `tools/menu/` | ~50 KB | Yes (rebuilt when sources are newer) |
| `share/games/HELLO/`, `share/apps/HELLO/` demo packages | created locally | small | Yes (only when missing) |
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
├── install.py          # downloads QEMU + MSN + RTL8029 + Watcom/PDCurses, builds MENU.EXE
├── start.py            # launches SMB server + QEMU (auto-calls install.ensure_all)
├── setup_net.py        # offline MS Network Client install into dos622_c.img
├── floppy.py           # runtime floppy insert/eject via QMP
├── smb_host.py         # stdlib-only SMB1 server (host-side)
├── tools/
│   └── menu/           # sources of the in-guest MENU.EXE launcher
├── deps/               # created by install.py: qemu/, watcom/, pdcurses/
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
    └── share/                  # ← host folder, exposed as F: (auto-created, gitignored)
        ├── HELLO.TXT
        ├── menu/MENU.EXE       # built from tools/menu/ by install.py
        ├── games/HELLO/        # demo package (runs HELLO.BAT)
        └── apps/HELLO/         # demo package (runs HELLO.EXE)
```

## CLI flags

```
python install.py --check      # report missing deps without installing
python install.py --qemu-only  # legacy: only install QEMU
python install.py --build-tools   # install Watcom/PDCurses + force-rebuild MENU.EXE / HELLO.EXE
python install.py --reseed-hello  # restore the HELLO demo packages (overwrites edits)
python install.py --system-dir systems/dos622  # target a different system

python start.py --skip-deps dos622   # bypass dep check (e.g. air-gapped)

python setup_net.py dos622 --test    # offline network install + boot test
```

## Notes & gotchas

- **First run is slow** — ~250 MB of QEMU plus the Open Watcom toolchain is
  downloaded on the first boot. Subsequent runs are fast.
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
| `f:\menu\menu.exe` → "Bad command or file name" or a blank blue screen | menu never built / stale build | `python install.py --build-tools` (rebuilds from `tools/menu/`) |
| `Error 53` computer name not found | LMHOSTS missing/wrong | Re-apply `10.0.2.2 smbserver  #PRE\r\n` to `C:\NET\LMHOSTS` in the image |
| `Error 66` network resource type incorrect | Stale `smb_host.py` | Update to the current version (uses `Service="A:"` for disk shares) |
| Boot hangs at "Loading PCI Ethernet..." | EMM386 / UMB missing, or `LH` on TSRs | `DOS=HIGH,UMB` in CONFIG.SYS; remove `LH` prefixes from AUTOEXEC |
| "DOS 6.22 floppies missing" warning | User-provided media absent | Place `dos622_1..4.img` in `systems/dos622/`, or ignore (boots from HDD) |

## Credits

- QEMU Windows builds: https://qemu.weilnetz.de/w64/ (Stefan Weil)
- MS Network Client 3.0b: https://archive.org/details/msnc3 (Microsoft, 1994)
- RTL8029 NDIS2 driver: https://archive.org/details/pcind.dos (Realtek, ~2000)
- Open Watcom V/2 compiler: https://github.com/open-watcom/open-watcom-v2
- PDCurses (menu TUI): https://github.com/wmcbrine/PDCurses (Paul McBride)
- MS-DOS 6.22 setup disks: user-supplied 4 × 1.44 MB images from a 1994
  retail set (not distributed with this project)
- SMB1 wire format: Microsoft MS-CIFS spec [MS-CIFS]-250210
