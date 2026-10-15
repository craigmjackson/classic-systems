# AGENTS.md — classic-systems

## What this is

A QEMU-based MS-DOS 6.22 emulator with a live two-way SMB1 share so the host
directory `systems/dos622/share/` appears as `F:` inside the guest.

## File roles

| File | Role |
|------|------|
| `install.py` | Downloads the latest `qemu-w64-setup-*.exe` from `qemu.weilnetz.de/w64/` and extracts it into `deps/qemu/AMD64/` |
| `start.py` | Loads `systems/<name>/package.yaml`, launches an SMB host server, builds a QEMU command, runs QEMU, then terminates the server. Usage: `python start.py [system]` |
| `floppy.py` | Runtime floppy control via QMP (port from `qemu.qmp_port`). Usage: `python floppy.py <system> eject`, `... insert <img>`, `... query` |
| `smb_host.py` | Stdlib-only SMB1 server (supports both NT LM 0.12 and DOS LANMAN dialects; PCnet/RTL8029 NDIS2-driver compatible). Logs every request. Launched as a child of `start.py` |
| `systems/dos622/package.yaml` | Spec for the MS-DOS 6.22 system |
| `systems/dos622/dos622_{1..4}.img` | MS-DOS 6.22 setup floppy images (from a 1994 retail set) |
| `systems/dos622/dos622_c.img` | 2 GB raw FAT16 C: drive |
| `systems/dos622/msclient/` | MS Network Client 3.0b disks + RTL8029 OEM driver + bootstrap floppies (`config.img`, `swapnet.img`, `pcind.img`) |
| `systems/dos622/share/` | Host-side folder exposed as guest `F:` |
| `systems/dos622/smb_host.log` | Server request log (append-only) |

## package.yaml schema

```yaml
name: dos622
description: MS-DOS 6.22
qemu:
  binary: qemu-system-x86_64.exe
  machine: pc
  cpus: 1
  memory_mb: 64
  boot_order: ac         # 'ac' = floppy then HDD; 'a'/'b' stripped when no floppy drive defined
  qmp_port: 4444
  drives:                # order matters; first floppy entry defines boot floppy
    - interface: floppy
      device: fda
      file: dos622_1.img
    - interface: ide
      file: dos622_c.img
      size: 2048M
  args: []               # runtime-only overrides (e.g. `-display none` from test harness)
network:
  nic: ne2k_pci          # MSN's RTL8029 NDIS2 driver binds to QEMU's ne2k_pci
  smb_dir: share         # relative to systems/<name>/; host folder
  smb_name: HOST         # share name on the wire
  smb_port: 139          # host TCP port; server binds 127.0.0.1 only
```

## start.py lifecycle

1. Build the QEMU command (no guestfwd, no chardev tricks — see gotchas below).
2. `Popen` the SMB server; probe readiness with `socket.create_connection(('127.0.0.1', port), timeout=0.2)` up to 5 s.
3. `subprocess.run` QEMU (blocking until the window closes).
4. `terminate()` the server in a `finally`; escalate to `kill()` after 5 s.

## QMP / guest I/O notes

- QEMU 11 `send-key`: use `{"type":"qcode","data":"<key>"}`. The legacy `hexnumber` format is rejected.
- `qcode` values: lowercase letters/digits directly (`"a"`, `"5"`); special names include `"spc","ret","tab","esc","up","down","left","right","home","pgup","pgdn","end","f1".."f12","backslash","dot","colon","semicolon","comma","minus","equal","slash","bracket_left","bracket_right","apostrophe","backquote"`.
- Shifted / uppercase characters: send a two-element key array in one call, e.g. `[{"type":"qcode","data":"shift"},{"type":"qcode","data":"a"}]`.
- `screendump <file>` works with `-display none` (VGA is still emulated); saves a PPM. The test harness converts PPM→PNG via a tiny zlib/struct writer.
- `system_reset` is instant but SeaBIOS needs ~6 s before QMP is ready again; `quit` is instantaneous.
- `-boot order=ac` + empty `-fda ""` boots the HDD cleanly: SeaBIOS falls through the empty floppy. If `package.yaml` lists no floppy drive, `start.py` strips `'a'`/`'b'` from `boot_order` so SeaBIOS doesn't show the scary "Boot failed: could not read the boot disk" message.
- `floppy.py eject` strips the floppy entry from `package.yaml` (as designed — matches the "floppy device present, but empty when no drive configured" rule). `floppy.py insert` re-adds it; so after eject, the next `start.py` launch boots from HDD only.

## Hard-learned gotchas

### QEMU build & networking

- **QEMU 11.1 for Windows has no built-in SMB server.** The `smb=` netdev option parses but is a no-op; slirp doesn't serve SMB on this build.
- **`guestfwd -cmd:python ...` fails on Windows.** QEMU's `fork_exec` helper is POSIX-only; error: `Slirp: fork_exec: Failed to execute helper program (No such file or directory)`.
- **`guestfwd -chardev:smbfwd` with a socket chardev is broken on Windows.** The guest connects, QEMU accepts, but slirp's chardev poll path hits `failed to WSAEventSelect(): An operation was attempted on something that is not a socket.` and never forwards the NEGOTIATE bytes.
- **Working path**: launch our own server on `127.0.0.1:139`; rely on slirp's built-in NAT for `10.0.2.2:139` → host loopback. No `guestfwd` at all. `package.yaml` has **no** `guestfwd` or `smb_guest_ip` fields.
- `start.py` binds the server to `127.0.0.1` only; the guest must reach it via `10.0.2.2` (slirp's host alias).

### NIC / driver pairing

- MS Network Client 3.0b bundles NDIS2 `ms$ne2clone` (driver `MS2000$`) — the ISA NE2000 clone. On QEMU's `ne2k_isa` at io=0x300/irq=5, **this driver truncates TX at 60 bytes** (sets TCNT too small; probably a probe bug treating 32 KB SRAM as 16 KB). No PROTOCOL.INI knob (`SRAMSIZE`, `IOTYPE`, `DMACHAN`, `SlotNumber`) fixes it.
- **Use `ne2k_pci`** (QEMU's Realtek RTL8029AS, PCI) with Realtek's `PCIND.DOS` NDIS2 driver (`DriverName=PCIND$`). The driver is on archive.org item `pcind.dos` (`RTL8029_Ndis2_v2.20.zip`), contains `DOS/PCIND.DOS`, `DOS/RTL8029.NIF`, `DOS/OEMSETUP.INF`.
- PROTOCOL.INI adapter block: `[RTL8029]\r\nDriverName=PCIND$\r\n` — **no** `IOADDRESS`/`INTERRUPT`/`DMACHANNEL` on PCI.
- SYSTEM.INI `[network drivers]`: `netcard=pcind.dos` and `pcind.dos=LOW`.
- QEMU's `ne2k_isa` default IRQ is **5**, not 9. ISA NE2000 iobase default is 0x300.

### DOS memory & boot

- MS Network Client stack + NIC driver + TCP/IP + LAN Manager + redirector ≈ 450 KB. Without UMBs, conventional memory is exhausted → `Error 8: There is not enough memory available.`, workstation never starts, NET0111/NET0116 errors.
- **Required CONFIG.SYS**:
  ```
  DEVICE=C:\DOS\SETVER.EXE
  DEVICE=C:\DOS\HIMEM.SYS
  DEVICE=C:\DOS\EMM386.EXE RAM
  DOS=HIGH,UMB
  FILES=30
  device=C:\NET\ifshlp.sys
  LASTDRIVE=Z
  ```
- **AUTOEXEC order matters** (each step depends on the previous):
  ```
  SMARTDRV.EXE /X
  net initialize         # PCIND driver banner
  netbind.com            # binds NDIS transports to NIC
  umb.com                # enables upper-memory block support
  tcptsr.exe             # TCP/IP TSR
  tinyrfc.exe            # RFC helper
  nmtsr.exe              # NetBIOS TSR (reads LMHOSTS here!)
  emsbfr.exe             # EMS buffer
  net start              # launches redirector (may show logon dialog)
  call c:\net\netmap.bat # last! maps F:
  ```
  Do **not** use `LH` on these — UMB is already enabled by `DOS=HIGH,UMB` + `umb.com`; `LH netbind.com` actually **hangs** the boot on this configuration.
- `NETMAP.BAT` must be the LAST line. MS Client Setup rewrites AUTOEXEC on reinstall and may reorder appended lines.
- Running `A:\SETUP` from a clean boot fails with "Setup has run out of memory." Workaround: boot with **F5** (bypasses CONFIG.SYS/AUTOEXEC), then run `A:\SETUP`. After Setup rewrites AUTOEXEC, re-apply the slimmed stack.
- `net use f: \\SMBSERVER\HOST /persistent:no` in NETMAP.BAT avoids the "already in use" prompt when SYSTEM.INI's `reconnect=yes` pre-maps F: during `net start`.

### SMB1 server (`smb_host.py`)

- LANMAN flavor (DOS client's "DOS LM1.2X002") negotiate response is **13 words**, NOT 17. Word layout via `<HHHHHHIHHHBBH`: `[dialect_idx, secmode=0x03, max_mpx=65535, max_vcs=1, max_buf=65535, max_raw=0, server_pid, dos_time, dos_date, pad=0, challenge_len=8, pad=0]`. flags byte = `0xA0` (REPLY|LOCK_AND_READ), **not** `0x98`. No domain string in body; body is the 8-byte challenge.
- NT LM 0.12 response is the standard 17 words + 34-byte param blob + challenge + UTF-16 domain.
- DOS client does **not** set `SMB_FLAGS2_NT_STATUS` (flags2 = `0x0000`). Error responses MUST use DOS-class codes (packed as 32-bit NTSTATUS: `class:code`). Map in `NT_TO_DOS`: `0xC000000F/0xC0000034 → 0x00020001`, `0xC000003A → 0x00030001`, `0xC0000022/0xC0000005 → 0x00050001`, `0xC0000035 → 0x00500001`.
- **TREE_CONNECT_ANDX** response: `Service` string (in `SMB_Data`) must be `"A:"` for disk shares per MS-CIFS §3.2.5.4 — anything else (including the share name) is classified as non-disk and the client returns **Error 66**. For `IPC$` use `"IPC"`. `NativeFileSystem` = `"FAT"` for DOS-era clients. `OptionalSupport = 0x0001` (SMB_SUPPORT_SEARCH_BITS).
- **SESSION_SETUP_ANDX** LANMAN response: `Action = 0` (authenticated). `Action = 1` (guest login) is rejected by the DOS redirector for drive mapping.
- **SMB_COM_SEARCH** (0x81) response data has a `0x05` BufferFormat byte + 2-byte DataLength header before the entries. Each entry is exactly **43 bytes**: 4-byte resume key (`0x04` + u32 index) + 16-byte 8.3 name (SPACE-padded, NOT NUL-padded) + 1-byte attrs + 2-byte DOS time + 2-byte DOS date + 4-byte size + 13-byte short name (SPACE-padded + NUL terminator).
- `SMB_COM_SEARCH` continuation: client echoes a *transformed* resume key (not the raw index we sent). Use a **server-side cursor keyed by TID** — never trust the echoed resume value.
- `SMB_COM_CHECK_DIRECTORY` (0x10) request data starts with a `0x04` BufferFormat byte; skip it before parsing the path.
- `SMB_COM_WRITE` (0x0B): words `[FID, Count, OffsetLo, OffsetHi, Reserved]`; data appended at end of body.
- `SMB_COM_READ` (0x0A): response words `[Count, Reserved, Reserved, Reserved, Reserved]`; data follows body.
- `SMB_COM_QUERY_INFORMATION` (0x08): DOS redirector sends this BEFORE write to validate the file. `LastWriteTime` is UTIME (32-bit Unix seconds as two u16 lo/hi), **not** DOS date/time.
- `SMB_COM_OPEN_ANDX` (0x2D): if the request `Flags` word has bit 0 set, include file info (attrs + UTIME + size) in the response; otherwise fields after FID MUST all be zero or the client rejects the response.
- `SMB_COM_CLOSE` (0x04): simple FID close, no response data.
- `SMB_COM_QUERY_INFORMATION_DISK` (0x80): response words `[TotalUnits, BlocksPerUnit, BytesPerBlock, FreeUnits, Reserved]`.
- `find_next` via `SMB_COM_SEARCH` with the client's transformed resume key: ignore the key, just return the next chunk from the server-side cursor.
- The `flags2 & 0x8000` (UNICODE) bit: client never sets it for LANMAN dialects. Encode strings accordingly (CP437/OEM, not UTF-16LE).
- `0x00` SMB_COM_CLOSE_AND_TREE_DISCONNECT: not required; client uses explicit `TREE_DISCONNECT` (0x71).
- `0x03` SMB_COM_TREE_DISCONNECT: no params, no data, ack with empty response.

### FAT image surgery

- The repo uses custom FAT12/FAT16 helpers (in the temp harness dir, `C:\Users\craig\AppData\Local\Temp\opencode\fatimg.py` and `rewrite_file.py`) for read-only inspection and byte-precise patches of `dos622_c.img`. They're not committed but are referenced by the implementation notes above.
- Patches require: (a) find the file's cluster chain via `data_cluster_off()`, (b) rewrite bytes in-place (new content must fit within existing chain), (c) update the directory entry size field at the correct 8.3-offset in the parent directory (**use exact 11-byte 8.3 match, not prefix** — `PROTOCOL.INI` and `PROTOCOL.001` share a prefix).
- For extensionless files (`LMHOSTS`, `HOSTS`, `NETWORKS`, `PROTOCOL`), the 8.3 entry has an empty extension field (11 bytes of name, no dot). `rewrite_file.py` handles this via `name.rpartition(".")` with fallback.

### External sources (reproducible)

- MS Network Client 3.0b (2 floppy images): archive.org item **`msnc3`** → `Disk1.img`, `Disk2.img`.
- RTL8029 NDIS2 driver: archive.org item **`pcind.dos`** → `RTL8029_Ndis2_v2.20.zip` (contains `DOS/PCIND.DOS`, `DOS/RTL8029.NIF`, `DOS/OEMSETUP.INF`).
- QEMU Windows binaries: `https://qemu.weilnetz.de/w64/` (latest `qemu-w64-setup-<date>.exe`).
- These are NOT committed to the repo (images are ~1.5 MB each, driver zip is 15 KB, QEMU is ~250 MB).

### What does NOT work (dead ends worth recording)

- `smb_host.py --stdio` + `guestfwd -cmd:python ...`: Windows has no `fork_exec`.
- `guestfwd -chardev:smbfwd` with a socket chardev: data is never forwarded past the initial NBT session request (Windows chardev poll breaks).
- MSN's bundled `ms$ne2clone` NDIS2 driver on `ne2k_isa`: TX truncation at 60 bytes.
- impacket SMB server: Defender quarantines `epm.py`, `__init__.py`, `DumpNTLMInfo.py` on install.
- QEMU `filter-dump` on Windows occasionally truncates pcap `incl_len` to 60 while `orig_len` is larger; always read `orig_len`.

## Boot sequence reference (current working DOS 6.22)

```
SeaBIOS → HDD
Starting MS-DOS...
HIMEM is testing extended memory...done.
C:\>C:\DOS\SMARTDRV.EXE /X
Loading PCI Ethernet Network Adapter ......... Network driver (U2.2) is installed
Microsoft DOS TCP/IP Protocol Driver 1.0a
Copyright (c) Microsoft Corporation, 1991.  All rights reserved.
...
Microsoft DOS TCP/IP NEMM Driver 1.0
The command completed successfully.
Microsoft(R) NWLINK Version 1.0
Copyright(C) Microsoft 1994.  All rights reserved.
MS-DOS LAN Manager v2.1 Netbind
NETWORK Ethernet board's Ethernet Address:525400123456
Microsoft DOS TCP/IP 1.0a
The command completed successfully.
The command completed successfully.
F: connected to \\SMBSERVER\HOST..
The command completed successfully.
C:\>
```

## TODO / future polish

- Automate MS Client setup end-to-end (was interactive this round; UNATTEND.TXT for 3.0b is undocumented — scripted sendkey with screenshot checkpoints is the most reliable path).
- Move `systems/dos622/msclient/*.img` to a dedicated bootstrap dir if the set grows.
- Add a `.gitignore` for `*.log`, `__pycache__/`, `deps/qemu/`, and the 2 GB `dos622_c.img` (user may want to keep it outside repo).
- Consider adding a `-display none` flag to `start.py` for headless automation.
