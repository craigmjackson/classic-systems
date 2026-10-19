# AGENTS.md — classic-systems

## What this is

A QEMU-based MS-DOS 6.22 emulator with a live two-way SMB1 share so the host
directory `systems/dos622/share/` appears as `F:` inside the guest.

## File roles

| File | Role |
|------|------|
| `install.py` | Installs all dependencies: downloads QEMU from `qemu.weilnetz.de/w64/`, MS Network Client from `archive.org/msnc3`, RTL8029 driver from `archive.org/pcind.dos` (and builds `pcind.img`), creates the blank 2 GB C: drive via `qemu-img`, seeds `share/HELLO.TXT`, builds `share/menu/MENU.EXE` + `share/apps/HELLO/HELLO.EXE` from `tools/menu/` (staleness-checked on every run; `--build-tools` forces), and seeds the `share/games|apps/HELLO/` demo packages only when absent (`--reseed-hello` forces). Exposes an importable `ensure_all(system_dir)` function that `start.py` calls. Also provides `--check` (report-only) and `--qemu-only` (legacy) CLI modes. |
| `start.py` | Loads `systems/<name>/package.yaml`, auto-runs `install.ensure_all()` (unless `--skip-deps`), launches the SMB host server, builds the QEMU command, runs QEMU (blocking), then tears down the server. Usage: `python start.py [system] [--skip-deps]` |
| `setup_net.py` | Host-side offline installer of the full MS Network Client stack into `dos622_c.img` (extracts from `msclient/*.img`, writes 36 files incl. C:\NET binaries, SYSTEM.INI/PROTOCOL.INI/AUTOEXEC/NETMAP, embedded WFWSYS.CFG + SHARES.PWL + DOS622.PWL). No guest SETUP run needed. Usage: `python setup_net.py dos622 [--check|--dry-run|--force|--test]`. `--test` boots headless and verifies F: write end-to-end (waits for the guest's TREE_CONNECT in `smb_host.log`, then types `copy c:\config.sys f:\zz.txt` and compares host bytes). |
| `floppy.py` | Runtime floppy control via QMP (port from `qemu.qmp_port`). Usage: `python floppy.py <system> eject`, `... insert <img>`, `... query`. `eject`/`insert` fall back to editing `package.yaml` alone when no VM/QMP is listening; `query` still requires a running VM. |
| `smb_host.py` | Stdlib-only SMB1 server (supports both NT LM 0.12 and DOS LANMAN dialects; PCnet/RTL8029 NDIS2-driver compatible). Logs every request. Launched as a child of `start.py` on `127.0.0.1:139`. |
| `systems/dos622/package.yaml` | Spec for the MS-DOS 6.22 system |
| `systems/dos622/dos622_{1..4}.img` | MS-DOS 6.22 setup floppy images — **user-provided**, not auto-downloaded (copyrighted retail media) |
| `systems/dos622/dos622_c.img` | 2 GB raw FAT16 C: drive (auto-created by `install.py`) |
| `systems/dos622/msclient/` | Auto-populated by `install.py`: `Disk1.img`, `Disk2.img` (MSN 3.0b), `pcind.img` (RTL8029 NDIS2 driver floppy). Also contains two manual recovery floppies: `config.img` (static IP + LMHOSTS), `swapnet.img` (driver + PROTOCOL.INI patcher) — not auto-generated. |
| `systems/dos622/share/` | Host-side folder exposed as guest `F:`; auto-seeded with `HELLO.TXT` by `install.py` (entire tree is gitignored — generated/seed content) |
| `tools/menu/` | Source of the in-guest TUI launcher: `menu.c` (PDCurses screens/dialogs), `package.c` (PACKAGE.INI scan/save/delete + run), `inifile.c/.h`, `package.h`. Built by `install.py` into `share/menu/MENU.EXE` — sources tracked, binaries gitignored. |
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
  BUFFERS=10
  DEVICEHIGH=C:\NET\ifshlp.sys
  LASTDRIVE=F
  ```
  (`DEVICEHIGH=ifshlp` must come after EMM386 so UMBs exist; `SETVER` stays `DEVICE=` because it loads on line 1 before HIMEM/EMM386. `LASTDRIVE=F` is enough — only C: and F: are used; Z wastes ~2 KB on drive-letter tables G-Z.)
- **AUTOEXEC order matters** (each step depends on the previous):
  ```
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
  Do **not** use `LH` on any of these — empirically, `LH` on `netbind.com` **and** `tcptsr.exe` both hang the boot (MS Client internals assume conventional-memory load addresses). TINYRFC/EMSBFR/TCPDRV/NEMM already self-allocate into UMBs after loading low, so LH is redundant even where it doesn't hang. The one win: `DEVICEHIGH=C:\NET\ifshlp.sys` (4 KB → upper) and SMARTDRV is **not used at all** (QEMU's host caching makes it redundant; it cost ~12 KB conventional + 16 KB upper for zero benefit under QEMU).
- **Largest executable program size is ~472 KB** with this stack (was 441 KB before dropping SMARTDRV, DEVICEHIGH=ifshlp, BUFFERS=10, LASTDRIVE=F). EMS stays fully available (EMM386 RAM). The remaining hogs are TCPTSR (42 KB) + REDIR (97 KB) — both need contiguous UMB space larger than the ~8 KB free block in the current UMA layout (SeaBIOS eats E000-FFFF, EMS page frame takes D000, only C800-CFFF ≈ 32 KB hole exists for UMBs, mostly filled by IFSHLP/REDIR's self-allocated upper portion). Fitting them would require `EMM386 NOEMS` (sacrifices EMS for games) — not worth it; 472 KB covers essentially all VGA-era titles.
- `NETMAP.BAT` must be the LAST line. MS Client Setup rewrites AUTOEXEC on reinstall and may reorder appended lines.
- Running `A:\SETUP` from a clean boot fails with "Setup has run out of memory." Workaround: boot with **F5** (bypasses CONFIG.SYS/AUTOEXEC), then run `A:\SETUP`. After Setup rewrites AUTOEXEC, re-apply the slimmed stack.
- `net use f: \\SMBSERVER\HOST /persistent:no` in NETMAP.BAT avoids the "already in use" prompt when SYSTEM.INI's `reconnect=yes` pre-maps F: during `net start`.

### SMB1 server (`smb_host.py`)

- LANMAN flavor (DOS client's "DOS LM1.2X002") negotiate response is **13 words**, NOT 17. Word layout via `<HHHHHHIHHHBBH`: `[dialect_idx, secmode=0x03, max_mpx=65535, max_vcs=1, max_buf=65535, max_raw=0, server_pid, dos_time, dos_date, pad=0, challenge_len=8, pad=0]`. flags byte = `0xA0` (REPLY|LOCK_AND_READ), **not** `0x98`. No domain string in body; body is the 8-byte challenge.
- NT LM 0.12 response is the standard 17 words + 34-byte param blob + challenge + UTF-16 domain.
- DOS client does **not** set `SMB_FLAGS2_NT_STATUS` (flags2 = `0x0000`). Error responses MUST use DOS-class codes (packed as 32-bit NTSTATUS: `class:code`). Map in `NT_TO_DOS`: `0xC000000F/0xC0000034 → 0x00020001`, `0xC000003A → 0x00030001`, `0xC0000022/0xC0000005 → 0x00050001`, `0xC0000035 → 0x00500001`.
- **TREE_CONNECT_ANDX** response: `Service` string (in `SMB_Data`) must be `"A:"` for disk shares per MS-CIFS §3.2.5.4 — anything else (including the share name) is classified as non-disk and the client returns **Error 66**. For `IPC$` use `"IPC"`. `NativeFileSystem` = `"FAT"` for DOS-era clients. `OptionalSupport = 0x0001` (SMB_SUPPORT_SEARCH_BITS).
- **SESSION_SETUP_ANDX** LANMAN response: `Action = 0` (authenticated). `Action = 1` (guest login) is rejected by the DOS redirector for drive mapping.
- **SMB_COM_SEARCH** (0x81) response data has a `0x05` BufferFormat byte + 2-byte DataLength header before the entries. Each entry is exactly **43 bytes**: 5-byte resume key (`0x04` + u32 index) + 16-byte 8.3 name (SPACE-padded, NOT NUL-padded) + 1-byte attrs (offset 21) + 2-byte DOS time + 2-byte DOS date + 4-byte size + 11-byte SPACE-padded 8.3 short name (NO dot between stem and ext) + attrs byte AGAIN (offset 41) + NUL (offset 42).
- `SMB_COM_SEARCH` continuation: client echoes a *transformed* resume key (not the raw index we sent). Use a **server-side cursor keyed by TID** — never trust the echoed resume value.
- `SMB_COM_SEARCH` new-vs-resume discriminator: the request path body either starts with `0x04`/printable ASCII (new search pattern → parse + listdir) or is a transformed resume key (non-printable → continue cursor). Never parse a resume key as a path.
- **DOS 8.3 wildcard matching (`dos_match`)**: both name and mask are fixed-width fields (stem 8, ext 3), space-padded. `*` in the mask must expand to `?`-fill for the REST of the field — never space-pad a wildcard (`"*".ljust(3)` → `"*  "` makes `fnmatch("INI", "*  ")` fail on the trailing spaces; this broke every `*.c`-style no-dot mask like `????????`, so `PACKAGE.INI` never matched and package deletion's file search returned 0 entries while dirs matched by luck). No-dot masks mean ext=`*`.
- `SearchAttributes` from the client (e.g. `0x10` for `_dos_findfirst(…, _A_SUBDIR)`) is parsed for logging and the volume-label special case only — entries are returned regardless of attrs (clients filter).
- `SMB_COM_CHECK_DIRECTORY` (0x10) request data starts with a `0x04` BufferFormat byte; skip it before parsing the path.
- `SMB_COM_WRITE` (0x0B): words `[FID, Count, OffsetLo, OffsetHi, Reserved]`; data appended at end of body. **Count=0 means DOS `AH=40h/CX=0`: truncate the file at OffsetLo** — Watcom CRT `fopen("wb")` opens with no create/truncate disposition and then issues this 0-byte write as its truncate; `fopen("ab")` never sends one (verified safe to truncate on it).
- `SMB_COM_READ` (0x0A): response words `[Count, Reserved, Reserved, Reserved, Reserved]`; data follows body.
- `SMB_COM_QUERY_INFORMATION` (0x08): DOS redirector sends this BEFORE write to validate the file. `LastWriteTime` is UTIME (32-bit Unix seconds as two u16 lo/hi), **not** DOS date/time.
- `SMB_COM_OPEN_ANDX` (0x2D): if the request `Flags` word has bit 0 set, include file info (attrs + UTIME + size) in the response; otherwise fields after FID MUST all be zero or the client rejects the response. Request word layout as seen from MSNC: `w[3]` = desired access (0=RO, 1=WO, 2=RW), `w[8]` = open function — `0x12` (DOS `AH=3Ch` via COMMAND.COM, e.g. `echo >`) triggers the `open_mode & 0x0002` truncate branch in `cmd_2d`; `0x1` (Watcom CRT) does not, and the CRT relies on the Count=0 WRITE truncate instead.
- **Core SMB command numbers (per impacket's table, NOT modern NT numbering)**: `0x00` CREATE_DIRECTORY (mkdir), `0x01` DELETE_DIRECTORY (rmdir), `0x02` OPEN, `0x03` CREATE, `0x04` CLOSE, `0x05` FLUSH, `0x06` DELETE (unlink), `0x08` QUERY_INFORMATION, `0x0A` READ, `0x0B` WRITE, `0x12` SEEK, `0x71` TREE_DISCONNECT, `0x81` SEARCH. Note the client's dir/delete traffic uses `0x00`/`0x01`/`0x06` — the old AGENTS claim that `0x00`/`0x03` were "close/disconnect" was wrong.
- `cmd_00` (CREATE_DIRECTORY) is dual-purpose: body starts with `0x04` BufferFormat → parse path + `os.mkdir` (`FileExistsError` → `NT_NAME_COLLISION`, `OSError` → `NT_ACCESS_DENIED`); a legacy empty body (seen only at session close) → `self.closed = True` + empty ack. `cmd_01` = `os.rmdir` (non-empty dir → `NT_ACCESS_DENIED`, which is correct — clients must delete files first). `cmd_06` = `os.remove` (unlink) with the same `0x04` path prefix skip.
- `SMB_COM_CLOSE` (0x04): simple FID close, no response data.
- `SMB_COM_QUERY_INFORMATION_DISK` (0x80): response words `[TotalUnits, BlocksPerUnit, BytesPerBlock, FreeUnits, Reserved]`.
- `find_next` via `SMB_COM_SEARCH` with the client's transformed resume key: ignore the key, just return the next chunk from the server-side cursor.
- The `flags2 & 0x8000` (UNICODE) bit: client never sets it for LANMAN dialects. Encode strings accordingly (CP437/OEM, not UTF-16LE).
- `TREE_DISCONNECT`: the client never seems to send `0x71` explicitly (no such REQ in the log); do not special-case it. `cmd_05` (FLUSH number) arrives with seek-like words (only 3× ever) and is treated as a seek — harmless.

### setup_net.py (offline network install) & headless logon

- **`net start` logon prompt is the classic hang.** `autologon=yes` still shows
  `Type your user name, or press ENTER if it is DOS622:` when no user password
  list exists. Under `start.py` nobody presses ENTER → boot stalls inside
  `net start`/`netmap` forever. Fix: ship `NET/DOS622.PWL` (606 bytes, user
  DOS622, empty password, embedded as `DOS622_PWL_B64`) + SYSTEM.INI
  `[Password Lists] DOS622=C:\NET\DOS622.PWL` → logon completes silently.
  The client appends that `[Password Lists]` line itself when a PWL is created
  interactively — `build_configs()` must match the image or `verify` fails
  byte-exact (this exact mismatch happened: 527 vs 553 bytes).
- **Interactive logon dance** (only if the embedded PWL is removed):
  ENTER (accept DOS622), ENTER (blank password), `y` (create PWL), ENTER
  (confirm blank), ENTER — with ≥3 s between keys; keys sent <1 s apart get
  swallowed by the Y/N prompt transition.
- **Orphan `smb_host.py` squatters kill every test invisibly.** A crashed
  debug run can leave a stale `smb_host` holding `127.0.0.1:139` (Windows
  SO_REUSEADDR allows a second LISTEN). The zombie accepts the guest's TCP
  connection, sends nothing, never logs → every `net use` hangs with an empty
  `smb_host.log`. Before any test: kill python processes whose CommandLine
  matches `smb_host`, and verify `netstat -ano | findstr :139` shows only your
  fresh child.
- **`net use \\<ip>\<share>` gives fast Error 53** — the DOS redirector still
  does NBNS resolution of the `<share>`'s computer name even with an IP
  literal. Use the NetBIOS name (`\\smbserver\host`) resolved via the LMHOSTS
  `#PRE` entry; that path connects fine (confirmed full NEGOTIATE→SESSION→
  TREE_CONNECT→SEARCH→OPEN/WRITE traffic in `smb_host.log`).
- **`smb_host.py cmd_2d` (OPEN_ANDX) must not create missing files on
  read-only opens.** DOS COPY probes the target with a read open first; if the
  server creates it there, COPY sees "exists" and stops at
  `Overwrite F:\ZZ.TXT (Yes/No/All)?` (headless = hang, empty file left behind).
  Create only when write intent is set (`access & 0x0003` or
  `open_mode & 0x0002`); otherwise return `NT_NAME_NOT_FOUND` and let the
  client's create path send the write open.
- `run_test()` waits for `TREE_CONNECT` in `smb_host.log` (guest's NETMAP
  mapped F:) before typing the probe. The old blind `sleep(40)` typed
  `copy c:\config.sys f:\zz.txt` into the logon prompt (its chars became the
  username / a stray `create password-list?` answer).
- QMP `send-key` qcode gotchas: `colon` is **rejected** by QEMU 11 — send
  `[{"qcode","shift"},{"qcode","semicolon"}]`; `.` is `dot`. **`ins` is also
  invalid — the qcode is `insert`** (likewise `delete`, not `del`). Spacing keys
  <0.12 s apart drops characters. PowerTip: inline `python -c` through
  PowerShell mangles quotes — write debug scripts as .py files. Harness
  wrappers around `qmp.send({"execute":"send-key",…})` often return None and
  silently discard QMP `error` replies — a typo'd qcode then looks like "the
  key did nothing"; print `reply["error"]` when debugging missed keystrokes.
- `IPCONFIG.EXE` requires an argument (`ipconfig c:\net`) and prints
  `No DHCP data available` with `DisableDHCP=1` — that is **normal** for the
  static config; `ping 10.0.2.2` succeeding proves the stack + slirp path.
- PROTOCOL.INI space-separated octets (`IPAddress0=10 0 2 15`,
  `SubNetMask0=255 255 255 0`, `DefaultGateway0=10 0 2 2`) are correct for
  MS Client's TCPIP$ driver (verified by ping).

### MENU.EXE (in-guest PDCurses launcher)

- Built by `install.py` (`build_menu_exe`) from `tools/menu/{menu,package,inifile}.c`
  with Open Watcom: `wcc -ms -oneatx -bt=dos …` + `-k8192` stack. **`-k16384`
  left only 218 B `_memavl`** — PDCurses `newwin()` then returned NULL and the
  menu sat forever on a blue screen. Small model (`-ms`) needs
  `pdcdos.h`'s `__SMALL__`→`SMALL` mapping (PDCurses' DOS port checks
  `__SMALL__`, Watcom defines `SMALL`). Watcom quirks hit along the way:
  E1058 (initializers after stdio calls), no `-Fc`/`-S` output options
  accepted by the driver invocations used here.
- PDCurses: sources in `deps/pdcurses/` (pristine zip `deps/pdcurses-3.9.zip`),
  build via `wmake -f Makefile.wcc MODEL=s` **with cwd `deps/pdcurses/dos/`**.
  **Zip extraction preserves the original 2019 mtimes** — after re-extracting
  sources you MUST `del *.obj pdcurses.lib` before wmake, or wmake sees the
  old .objs as "newer" and silently skips the rebuild.
- `PACKAGE.INI` schema (written CRLF by `pkg_save`):
  `[package]\r\nname=%s\r\nexecutable=%s\r\nargs=%s\r\n`. Scanned from
  `F:\GAMES\<dir>\` and `F:\APPS\<dir>\`; sorted by name (stricmp).
- `pkg_run` EXEC rule: direct `spawnv` only when the exe extension is
  `.com`/`.exe` (case-insensitive last-4-chars check); everything else —
  including `.bat` — goes through `%COMSPEC% /c`. **DOS loads a `.bat` handed
  to it as a binary program**: a junk executable name (`hello.batTest Package`)
  got 8.3-mangled into the real `HELLO.BAT`, DOS executed its text as code →
  triple fault → QEMU rebooted to the SeaBIOS screenshot.
- Delete flow requires typing `DELETE` (strcmpi) after choosing full delete;
  the quick `y` path only removes `PACKAGE.INI`. `del_tree` walks with
  `_dos_findfirst(pat, _A_SUBDIR)` + `remove` + `_rmdir` — it depends on the
  `dos_match` 8.3 rule above to see `PACKAGE.INI` under the `????????` mask.
- `pkg_dialog` must repaint **every** field value on each outer-loop pass
  (labels alone is the bug that left unfocused fields blank until focused);
  `edit_field` returns `KEY_UP`/`KEY_DOWN`/`KEY_BTAB` as field navigation —
  `pkg_dialog` wraps with them, while `do_delete`'s confirm loop swallows them
  (its return value is otherwise ignored, so they must not close the dialog).
- E2E harnesses (ad-hoc, `C:\Users\craig\AppData\Local\Temp\opencode\`):
  `menu_smoke_test.py` (7 screens: scan/list/run bat/run exe/args/exit),
  `crud_test.py` (6 checks: list, edit→exact-bytes save incl. truncate, add→
  mkdir+INI-bytes, delete→DELETE+RMDIR, HELLO untouched), and
  `dialog_view_test.py` (4 screenshots: all-fields-painted-on-open, live
  typing echo, ↓/↑ field switching, Esc cancels without saving). All boot
  headless (`-display none`), spawn their own `smb_host.py`, wait for
  TREE_CONNECT+4 s.

### FAT image surgery

- The repo uses custom FAT12/FAT16 helpers (in the temp harness dir, `C:\Users\craig\AppData\Local\Temp\opencode\fatimg.py` and `rewrite_file.py`) for read-only inspection and byte-precise patches of `dos622_c.img`. They're not committed but are referenced by the implementation notes above.
- Patches require: (a) find the file's cluster chain via `data_cluster_off()`, (b) rewrite bytes in-place (new content must fit within existing chain), (c) update the directory entry size field at the correct 8.3-offset in the parent directory (**use exact 11-byte 8.3 match, not prefix** — `PROTOCOL.INI` and `PROTOCOL.001` share a prefix).
- For extensionless files (`LMHOSTS`, `HOSTS`, `NETWORKS`, `PROTOCOL`), the 8.3 entry has an empty extension field (11 bytes of name, no dot). `rewrite_file.py` handles this via `name.rpartition(".")` with fallback.

### External sources (reproducible)

- MS Network Client 3.0b (2 floppy images): archive.org item **`msnc3`** → `https://archive.org/download/msnc3/Disk1.img`, `.../Disk2.img`.
- RTL8029 NDIS2 driver: archive.org item **`pcind.dos`** → `https://archive.org/download/pcind.dos/RTL8029_Ndis2_v2.20.zip` (contains `DOS/PCIND.DOS`, `DOS/RTL8029.NIF`, `DOS/OEMSETUP.INF`; extracted and built into `pcind.img` by `install.py`).
- QEMU Windows binaries: `https://qemu.weilnetz.de/w64/` (latest `qemu-w64-setup-<date>.exe`).
- These are NOT committed to the repo (images are ~1.5 MB each, driver zip is 15 KB, QEMU is ~250 MB).

### What does NOT work (dead ends worth recording)

- `smb_host.py --stdio` + `guestfwd -cmd:python ...`: Windows has no `fork_exec`.
- `guestfwd -chardev:smbfwd` with a socket chardev: data is never forwarded past the initial NBT session request (Windows chardev poll breaks).
- MSN's bundled `ms$ne2clone` NDIS2 driver on `ne2k_isa`: TX truncation at 60 bytes.
- impacket SMB server: Defender quarantines `epm.py`, `__init__.py`, `DumpNTLMInfo.py` on install.
- QEMU `filter-dump` on Windows occasionally truncates pcap `incl_len` to 60 while `orig_len` is larger; always read `orig_len`.
- `LH` on MS Client TSRs: `netbind.com` and `tcptsr.exe` both hang the boot when loadhigh'd — keep the whole AUTOEXEC net stack low.
- `pcind.dos=HIGH` in SYSTEM.INI: the driver loads high fine, but it grabs the big UMB chunk first and REDIR's self-allocated upper portion shrinks from 16 KB to 4 KB — net wash (~1 KB worse). Keep `pcind.dos=LOW`.
- SMARTDRV under QEMU: redundant (host caches the raw image); cost 12 KB conventional + 16 KB upper for no benefit. Removed from AUTOEXEC.

## Boot sequence reference (current working DOS 6.22)

```
SeaBIOS → HDD
Starting MS-DOS...
HIMEM is testing extended memory...done.
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

Logon is **silent** now (embedded `DOS622.PWL`): no `Type your user name`
prompt appears; NETMAP maps `F:` and returns to `C:\>` hands-free.

## TODO / future polish

- ~~Automate MS Client setup end-to-end~~ — done: `setup_net.py` installs the
  whole stack offline into the image (no guest SETUP, no UNATTEND.TXT needed);
  `--test` verifies boot→F: write end-to-end.
- ~~Add a `.gitignore`~~ — done: `*.img`, `*.log`, `*.obj`, `*.err`, `*.map`,
  `__pycache__/`, `deps/`, and the whole `systems/*/share/` tree (all seed +
  build output; `tools/menu/` sources stay tracked).
- Consider adding a `-display none` flag to `start.py` for headless automation.
- Consider auto-detecting when `smb_host.py` crashes (Defender quarantine, missing port) and surfacing a clearer error than "connection refused".
- Factor `check_deps()` / `ensure_all()` so new systems (e.g. Win3.1, FreeDOS) can declare their own deps in `package.yaml` instead of being hard-coded to `dos622`.
