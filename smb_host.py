import argparse
import fnmatch
import os
import socket
import struct
import sys
import threading
import time
import traceback

NT_OK = 0
NT_NO_SUCH_FILE = 0xC000000F
NT_PATH_NOT_FOUND = 0xC000003A
NT_NAME_NOT_FOUND = 0xC0000034
NT_NAME_COLLISION = 0xC0000035
NT_ACCESS_DENIED = 0xC0000022
NT_EA_TOO_LARGE = 0xC000012C
NT_INFO_MISMATCH = 0xC00000F2

FILETIME_EPOCH = 11644473600.0
LOG_PATH = ""
LOG_LOCK = threading.Lock()
QUIET_STDOUT = False


def log(msg):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    with LOG_LOCK:
        if not QUIET_STDOUT:
            print(line, flush=True)
        if LOG_PATH:
            with open(LOG_PATH, "a") as f:
                f.write(line + "\n")


def ft(unix_ts):
    return struct.pack("<Q", int((unix_ts + FILETIME_EPOCH) * 10000000))


def attrs_for(path, isdir):
    a = 0x10 if isdir else 0
    try:
        if not os.access(path, os.W_OK):
            a |= 1
    except OSError:
        pass
    return a if a else (0x10 if isdir else 0x20)


def dos_date(ts):
    y, m, d, *_ = time.localtime(ts)[:6]
    return (max(1, y - 1980) << 9) | (m << 5) | d


def dos_time(ts):
    _, _, _, h, mi, s, *_ = time.localtime(ts) + (0,)
    return (h << 11) | (mi << 5) | (s // 2)


NT_TO_DOS = {
    0xC000000F: 0x00020001,
    0xC0000034: 0x00020001,
    0xC000003A: 0x00030001,
    0xC0000035: 0x00500001,
    0xC0000022: 0x00050001,
    0xC0000005: 0x00050001,
    0xC0000002: 0x00010001,
    0xC00000BA: 0x00030001,
    0xC0000016: 0xC0000016,
}


class SmbConn(threading.Thread):
    dialect = "NT LM 0.12"
    unicode_resp = True

    def __init__(self, sock, peer, root, share_name):
        super().__init__(daemon=True)
        self.s = sock
        self.peer = peer
        self.root = root
        self.share = share_name
        self.uid = 0
        self.tids = {}
        self.files = {}
        self.fh_seq = 3
        self.tid_seq = 1
        self.searches = {}
        self.sh_seq = 1
        self.maxbuf = 16644
        self.closed = False

    def run(self):
        log(f"connection from {self.peer}")
        try:
            while not self.closed:
                hdr = self.recv_exact(4)
                if hdr is None:
                    break
                typ = hdr[0]
                if typ in (0x81, 0x82, 0x83, 0x84, 0x85):
                    ln = ((hdr[1] & 0x01) << 16) | (hdr[2] << 8) | hdr[3]
                else:
                    ln = (hdr[1] << 16) | (hdr[2] << 8) | hdr[3]
                log(f"  nbt header={hdr.hex()} type={typ:#x} len={ln}")
                body = self.recv_exact(ln) if ln else b""
                if body is None:
                    break
                if typ == 0x81:
                    log(f"  nbt session request -> accept")
                    self.send_nbt(0x82, b"")
                    continue
                if typ == 0x85:
                    self.send_nbt(0x85, b"")
                    continue
                if typ != 0x00:
                    log(f"unexpected NBT type {typ:#x} len={ln}")
                    break
                log(f"  nbt msg len={ln} sig={body[:8].hex()}")
                self.dispatch(body)
        except Exception:
            log(traceback.format_exc())
        try:
            self.s.close()
        except OSError:
            pass
        log(f"connection closed {self.peer}")

    def recv_exact(self, n):
        buf = b""
        while len(buf) < n:
            try:
                ch = self.s.recv(n - len(buf))
            except OSError:
                return None
            if not ch:
                return None
            buf += ch
        return buf

    def send_nbt(self, typ, body):
        self.s.sendall(bytes([typ]) + struct.pack(">I", len(body))[1:] + body)

    def parse(self, d):
        cmd = d[4]
        status = struct.unpack("<I", d[5:9])[0]
        flags = d[9]
        flags2 = struct.unpack("<H", d[10:12])[0]
        tid, pid, uid, mid = struct.unpack("<HHHH", d[24:32])
        wc = d[32]
        words = [struct.unpack("<H", d[33 + 2 * i:35 + 2 * i])[0] for i in range(wc)]
        boff = 33 + 2 * wc
        bc = struct.unpack("<H", d[boff:boff + 2])[0] if boff + 2 <= len(d) else 0
        body = d[boff + 2:boff + 2 + bc]
        return dict(cmd=cmd, status=status, flags=flags, flags2=flags2,
                    tid=tid, pid=pid, uid=uid, mid=mid, wc=wc, words=words,
                    body=body, raw=d)

    def reply(self, r, words, body=b"", status=NT_OK, raw_setup=0, params=None, tid=None, uid=None, flags=0x98):
        if status != NT_OK and not (r["flags2"] & 0x4000):
            status = NT_TO_DOS.get(status, 0x00010001)
        hdr = b"\xffSMB" + bytes([r["cmd"]]) + struct.pack("<I", status) + bytes([flags]) \
            + struct.pack("<H", r["flags2"]) + struct.pack("<H", 0) + b"\x00" * 8 + b"\x00" * 2 \
            + struct.pack("<HHHH", r["tid"] if tid is None else tid, r["pid"], r["uid"] if uid is None else uid, r["mid"])
        if params is None:
            ws = b"".join(struct.pack("<H", x) for x in words)
        else:
            ws = params
        return hdr + bytes([len(ws) // 2]) + ws + struct.pack("<H", len(body)) + body

    def send(self, pkt):
        if os.environ.get("SMB_HEXDUMP"):
            log(f"  send {len(pkt)} bytes: {pkt[:96].hex()}")
        self.send_nbt(0x00, pkt)

    def dispatch(self, d):
        r = self.parse(d)
        cmd = r["cmd"]
        names = {0x72: "NEGOTIATE", 0x73: "SESSION_SETUP_ANDX", 0x75: "TREE_CONNECT_ANDX",
                 0x76: "TREE_DISCONNECT", 0x32: "TRANS2", 0x25: "TRANS", 0x2D: "OPEN_ANDX", 0x2E: "READ_ANDX",
                 0x0A: "READ", 0x0B: "WRITE", 0x08: "QUERY_INFORMATION", 0x10: "CHECK_DIRECTORY",
                 0x2F: "WRITE_ANDX", 0x04: "CLOSE", 0x06: "DELETE", 0xA2: "NT_CREATE_ANDX",
                 0x2B: "ECHO", 0x24: "LOCKING_ANDX", 0x02: "CREATE", 0x0A: "ECHO2",
                 0x05: "SEEK", 0xD4: "LOCKX", 0x00: "MKDIR", 0x01: "RMDIR", 0x03: "CREATE2"}
        log(f"REQ  {names.get(cmd, hex(cmd))} uid={r['uid']} tid={r['tid']} wc={r['wc']} bc={len(r['body'])} flags2={r['flags2']:#x}")
        fn = self.cmd_25 if cmd == 0x32 else getattr(self, f"cmd_{cmd:02x}", None)
        if fn:
            try:
                fn(r)
            except Exception:
                log(traceback.format_exc())
                self.send(self.reply(r, [], status=0xC0000005))
        else:
            log(f"  unhandled cmd, returning not-supported")
            self.send(self.reply(r, [], status=0xC0000002))

    def cmd_72(self, r):
        b = r["body"]
        parts = b.split(b"\x02")
        dialects = [p.rstrip(b"\x00").decode("latin1") for p in parts[1:] if p and p.strip(b"\x00")]
        log(f"  dialects={dialects}")
        self.ext_sec = bool(r["flags2"] & 0x0800)
        chosen, idx, flavor = None, 0, "nt1"
        if "NT LM 0.12" in dialects:
            chosen, idx, flavor = "NT LM 0.12", dialects.index("NT LM 0.12"), "nt1"
        elif "DOS LM1.2X002" in dialects:
            chosen, idx, flavor = "DOS LM1.2X002", dialects.index("DOS LM1.2X002"), "lanman2"
        elif "MICROSOFT NETWORKS 3.0" in dialects:
            chosen, idx, flavor = "MICROSOFT NETWORKS 3.0", dialects.index("MICROSOFT NETWORKS 3.0"), "lanman1"
        elif dialects:
            chosen, idx, flavor = dialects[-1], len(dialects) - 1, "lanman1"
        else:
            chosen, idx, flavor = "NT LM 0.12", 0, "nt1"
        self.dialect = chosen
        log(f"  chosen idx={idx} {chosen} flavor={flavor} ext_sec={self.ext_sec}")
        if flavor == "nt1":
            return self.reply_nt1(r, idx)
        return self.reply_lanman(r, idx)

    def reply_nt1(self, r, idx):
        now = int(time.time())
        secmode = 0x0B if self.ext_sec else 3
        params = struct.pack("<HBHHIIIIQHB", idx, secmode, 50, 1, 65535, 16644,
                             0, 0xF7, now & 0xFFFFFFFFFFFFFFFF, 0, 8 if not self.ext_sec else 0)
        if self.ext_sec:
            self.challenge = b"\x11" * 8
            body = os.urandom(16) + b""
        else:
            self.challenge = os.urandom(8)
            if r["flags2"] & 0x8000:
                body = self.challenge + "WORKGROUP".encode("utf-16-le") + b"\x00\x00"
            else:
                body = self.challenge + b"WORKGROUP\x00"
        self.send(self.reply(r, [], body, params=params))

    def reply_lanman(self, r, idx):
        now = time.time()
        self.challenge = os.urandom(8)
        params = struct.pack("<HHHHHHIHHHBBH", idx, 0x03, 65535, 50, 1, 0,
                             os.getpid() & 0xFFFFFFFF,
                             dos_time(now), dos_date(now), 0, 8, 0, 0)
        self.send(self.reply(r, [], self.challenge, params=params, flags=0xA0))

    def cmd_73(self, r):
        w = r["words"]
        wc = r["wc"]
        if self.ext_sec:
            blob_len = w[7] if wc >= 8 else 0
            blob_off = (w[8] | (w[9] << 16)) if wc >= 10 else 0
            blob = r["raw"][blob_off:blob_off + blob_len] if blob_off else b""
            if blob[:8] == b"NTLMSSP\x00" and struct.unpack("<I", blob[8:12])[0] == 1:
                type2 = (b"NTLMSSP\x00" + struct.pack("<I", 2)
                         + struct.pack("<HHI", 0, 0, 56)
                         + struct.pack("<I", 0x00008201)
                         + self.challenge + b"\x00" * 8
                         + struct.pack("<HHI", 0, 0, 56))
                self.uid = 1
                params = struct.pack("<BBHHH", 0xFF, 0, 0, 1, len(type2))
                self.send(self.reply(r, [], type2, status=0xC0000016, params=params))
                return
            self.uid = 1
            params = struct.pack("<BBHHH", 0xFF, 0, 0, 1, 0)
            self.send(self.reply(r, [], b"", params=params))
            return
        self.uid = 1
        params = struct.pack("<BBHH", 0xFF, 0, 0, 0)
        body = b"Windows for Workgroups 3.11\x00NETWORK\x00"
        self.send(self.reply(r, [], body, params=params, uid=1))

    def cmd_75(self, r):
        b = r["body"]
        log(f"  tree raw={b.hex()}")
        path = self.decode_string(b, r["flags2"])
        log(f"  tree path={path!r}")
        name = path.rsplit("\\", 1)[-1].upper().strip("\x00")
        if name in ("IPC$", "IPC"):
            t = 0xFFFF
        else:
            t = self.tid_seq
            self.tid_seq += 1
        self.tids[t] = (self.root, name)
        params = struct.pack("<BBHH", 0xFF, 0, 0, 0x0001)
        if name in ("IPC$", "IPC"):
            service, fs = "IPC", ""
        else:
            service, fs = "A:", "FAT"
        if r["flags2"] & 0x8000:
            data = service.encode("utf-16-le") + b"\x00\x00"
            if len(data) % 2:
                data += b"\x00\x00"
            data += fs.encode("utf-16-le") + b"\x00\x00"
        else:
            data = service.encode("latin1") + b"\x00" + fs.encode("latin1") + b"\x00"
        self.send(self.reply(r, [], data, tid=t, params=params))

    @staticmethod
    def decode_string(data, flags2):
        if flags2 & 0x8000:
            if data.startswith(b"\x00"):
                data = data[1:]
            end = next((i for i in range(0, len(data) - 1, 2) if data[i:i + 2] == b"\x00\x00"), len(data))
            return data[:end].decode("utf-16-le", "replace")
        return data.lstrip(b"\x00").split(b"\x00", 1)[0].decode("cp437", "replace")

    def cmd_76(self, r):
        self.tids.pop(r["tid"], None)
        self.send(self.reply(r, []))

    def parse_trans2(self, r):
        d = r["raw"]
        wc = r["wc"]
        base = 33 + 2 * wc
        tpc = r["words"][9] if wc > 9 else 0
        tp_off = r["words"][10] if wc > 10 else 0
        tdc = r["words"][11] if wc > 11 else 0
        td_off = r["words"][12] if wc > 12 else 0
        setup_count = r["words"][13] if wc > 13 else 0
        params = d[tp_off:tp_off + tpc]
        data = d[td_off:td_off + tdc]
        setups = r["words"][14:14 + setup_count]
        sub = setups[0] if setups else 0
        return sub, params, data, setups

    def send_trans2(self, r, params, data, setups):
        sc = len(setups)
        base = 33 + struct.calcsize("<HHHHHHHHHBB") + 2 * sc + 2
        p_off = base
        d_off = base + len(params)
        head = struct.pack("<HHHHHHHHHBB", len(params), len(data), 0,
                           len(params), p_off, 0, len(data), d_off, 0, sc, 0)
        params_data = head + b"".join(struct.pack("<H", x) for x in setups)
        hdr = b"\xffSMB" + bytes([0x32]) + struct.pack("<I", 0) + b"\x98" \
            + struct.pack("<H", r["flags2"]) + struct.pack("<H", 0) + b"\x00" * 8 + b"\x00" * 2 \
            + struct.pack("<HHHH", r["tid"], r["pid"], r["uid"], r["mid"])
        ws = params_data
        body = params + data
        pkt = hdr + bytes([len(ws) // 2]) + ws + struct.pack("<H", len(body)) + body
        log(f"  TRANS2 reply wc={len(ws)//2} p={params.hex()} dlen={len(data)} len={len(pkt)} p_off={p_off} d_off={d_off}")
        self.send(pkt)

    def trans_err(self, r, status):
        self.send(self.reply(r, [], status=status))

    def cmd_25(self, r):
        if r["tid"] not in self.tids:
            self.tids[r["tid"]] = (self.root, self.share)
        sub, params, data, setups = self.parse_trans2(r)
        log(f"  TRANS2 sub=0x{sub:04x} p={params[:16].hex()} data={data[:48]!r}")
        fn = {0x0001: self.t2_find_first, 0x0002: self.t2_find_next,
              0x000B: self.t2_find_close, 0x000D: self.t2_query_path,
              0x000E: self.t2_query_fs,
              0x000C: self.t2_set_file, 0x0006: self.t2_set_path,
              0x000F: self.t2_query_file,
              0x0000: self.t2_mkdir}.get(sub)
        if fn is None:
            log(f"  unhandled TRANS2 sub {sub:#x}")
            return self.trans_err(r, 0xC0000002)
        fn(r, params, data)

    def resolve(self, raw):
        return None

    def cur_root(self, r):
        entry = self.tids.get(r["tid"])
        return entry[0] if entry else self.root

    def _join_root(self, r, rel):
        """Resolve a client-relative path inside the share for this TID."""
        base = self.cur_root(r)
        parts = [p for p in rel.replace("/", "\\").split("\\") if p]
        if parts and parts[0].upper() in ("??", self.share.upper()):
            parts = parts[1:]
        full = os.path.normpath(os.path.join(base, *parts)) if parts else base
        if full != base and not full.startswith(base + os.sep):
            return base
        return full

    def path_of(self, r, raw):
        path = self.decode_string(raw, r["flags2"]).replace("/", "\\")
        return self._join_root(r, path)

    def find_entry(self, full, name, level, unicode_names):
        st = os.stat(full)
        isdir = os.path.isdir(full)
        size = st.st_size
        alloc = ((size + 4095) // 4096) * 4096
        ctime, atime, mtime = ft(st.st_ctime), ft(st.st_atime), ft(st.st_mtime)
        fixed = struct.pack("<I", 0) + struct.pack("<I", 0) + ctime + atime + mtime + mtime \
            + struct.pack("<Q", size) + struct.pack("<Q", alloc) \
            + struct.pack("<I", attrs_for(full, isdir)) + struct.pack("<I", 0) \
            + struct.pack("<I", 0)
        enc = name.encode("utf-16-le") if unicode_names else name.encode("cp437", "replace")
        if level == 0x0104:
            stem, ext = os.path.splitext(name.upper())
            short = (stem[:8] + ext[:4]).encode("utf-16-le") if unicode_names else (stem[:8] + ext[:4]).encode("cp437", "replace")
            short = short[:24].ljust(24, b"\x00")
            fixed += bytes([min(len(short.rstrip(b"\x00")), 255), 0]) + short
        fixed = bytearray(fixed)
        struct.pack_into("<I", fixed, 4 + 4 + 8 * 4 + 8 + 8 + 4, len(enc))
        entry = bytearray(fixed) + bytearray(enc)
        entry += b"\x00" * (4 - len(entry) % 4 if len(entry) % 4 else 0)
        struct.pack_into("<I", entry, 0, len(entry))
        return bytes(entry)

    def build_entries(self, names, target):
        out = []
        for n in names:
            full = os.path.join(target, n)
            try:
                out.append((n, full, os.stat(full)))
            except OSError:
                continue
        return out

    def t2_find_first(self, r, params, data):
        attrs, maxcnt, flags, level = struct.unpack("<HHHH", params[0:8])
        raw_pattern = params[12:] if len(params) > 12 else data
        if raw_pattern.startswith(b"\x04"):
            raw_pattern = raw_pattern[1:]
        pattern = self.decode_string(raw_pattern, r["flags2"]).strip("\x00")
        dpart, _, mask = pattern.replace("/", "\\").rpartition("\\")
        mask = mask or "*"
        target = self._join_root(r, dpart)
        log(f"  find_first attrs={attrs:#x} max={maxcnt} level={level:#x} pattern={pattern!r}")
        try:
            names = sorted(os.listdir(target))
        except OSError:
            return self.trans_err(r, NT_PATH_NOT_FOUND)
        if mask.upper() in ("*", "*.*", "?"):
            keep = names
        else:
            keep = [n for n in names if self.dos_match(n, mask)]
        sh = self.sh_seq
        self.sh_seq += 1
        self.searches[sh] = (keep, target)
        log(f"  find_first matched={keep}")
        self.find_response(r, sh, level, from_idx=0)

    def find_response(self, r, sh, level, from_idx):
        names, target = self.searches[sh]
        buf = b""
        count = 0
        i = from_idx
        while i < len(names):
            full = os.path.join(target, names[i])
            try:
                e = self.find_entry(full, names[i], level, bool(r["flags2"] & 0x8000))
            except OSError:
                i += 1
                continue
            if len(buf) + len(e) > 4000:
                break
            buf += e
            count += 1
            i += 1
        eos = 1 if i >= len(names) else 0
        if not eos:
            self.searches[sh] = (names[i:], target)
        params = struct.pack("<HHHHH", sh, count, eos, 0, 0)
        log(f"  find_response count={count} eos={eos} bytes={len(buf)}")
        self.send_trans2(r, params, buf, [])

    def t2_find_next(self, r, params, data):
        maxcnt, level, resume = struct.unpack("<HHH", params[0:6])
        name = data.split(b"\x00", 1)[0].decode("latin1", "replace")
        sh = resume
        if sh not in self.searches:
            for k in self.searches:
                sh = k
                break
        if sh not in self.searches:
            return self.trans_err(r, NT_NO_SUCH_FILE)
        self.find_response(r, sh, level, 0)

    def t2_find_close(self, r, params, data):
        sh = struct.unpack("<H", params[0:2])[0] if params else 0
        self.searches.pop(sh, None)
        self.send_trans2(r, b"", b"", [0x000B])

    def t2_query_path(self, r, params, data):
        level = struct.unpack("<H", params[0:2])[0]
        path = data.split(b"\x00", 1)[0]
        target = self.path_of(r, path)
        log(f"  query_path level={level:#x} target={target}")
        if not os.path.exists(target):
            return self.trans_err(r, NT_NAME_NOT_FOUND)
        info = self.file_info(os.stat(target), os.path.isdir(target), level)
        self.send_trans2(r, struct.pack("<H", len(info)), info, [0x000D])

    def t2_query_file(self, r, params, data):
        fid = struct.unpack("<H", params[0:2])[0]
        level = struct.unpack("<H", params[2:4])[0]
        kind, obj, target = self.files.get(fid, (None, None, r["tid"]))
        if not target or not os.path.exists(target):
            return self.trans_err(r, NT_NO_SUCH_FILE)
        info = self.file_info(os.stat(target), os.path.isdir(target), level)
        self.send_trans2(r, struct.pack("<HH", len(info), 0), info, [0x0010])

    def file_info(self, st, isdir, level):
        ctime, atime, wtime = ft(st.st_ctime), ft(st.st_atime), ft(st.st_mtime)
        size = st.st_size
        alloc = ((size + 4095) // 4096) * 4096
        a = attrs_for("", isdir)
        if level in (0x0101, 0x101, 1):
            return ctime + atime + wtime + wtime + struct.pack("<I", a) + b"\x00\x00\x00\x00"
        if level in (0x0102, 0x0201, 2, 0x102):
            return struct.pack("<I", 1) + struct.pack("<Q", size) + struct.pack("<Q", alloc) + struct.pack("<I", a) + ft(st.st_ctime) + atime + wtime + wtime
        if level in (0x0202, 3, 0x103):
            base = ctime + atime + wtime + wtime + struct.pack("<I", a) + b"\x00\x00\x00\x00" + struct.pack("<Q", size) + struct.pack("<Q", alloc) + struct.pack("<I", 0)
            return base + struct.pack("<I", 0) + b"\x00\x00\x00\x00"
        if level in (0x0401, 4, 0x104):
            return ctime + atime + wtime + wtime + struct.pack("<Q", size) + struct.pack("<Q", alloc) + struct.pack("<IIIIII", a, 0, 0, 0o777 if not isdir else 0o755, 1, 0)
        return ctime + atime + wtime + wtime + struct.pack("<I", a) + b"\x00\x00\x00\x00"

    def t2_query_fs(self, r, params, data):
        level = struct.unpack("<H", params[0:2])[0]
        log(f"  query_fs level={level:#x}")
        if level in (0x0100, 0x100, 0x0100):
            try:
                sv = os.statvfs(self.root)
                total, free, bsize = sv.f_blocks, sv.f_bavail, sv.f_bsize
                spc = 1024
                tc, fc = (total * bsize // 4096) or 1, (free * bsize // 4096) or 1
                return self.send_trans2(r, struct.pack("<H", 24), struct.pack("<IQIQ", spc, tc, fc, 512), [0x000E])
            except Exception:
                pass
            return self.send_trans2(r, struct.pack("<H", 24), struct.pack("<IQIQ", 8, 250000, 120000, 512), [0x000E])
        if level == 0x01:
            label = b"HOSTSHARE"
            info = struct.pack("<II", 11, len(label)) + label
            return self.send_trans2(r, struct.pack("<H", len(info)), info, [0x000E])
        if level in (0x0101, 0x101):
            return self.send_trans2(r, struct.pack("<H", 8), struct.pack("<II", 0x0000FF00, 255), [0x000E])
        return self.trans_err(r, 0xC0000002)

    def t2_mkdir(self, r, params, data):
        path = data.split(b"\x00", 1)[0]
        target = self.path_of(r, path)
        try:
            os.makedirs(target, exist_ok=True)
        except OSError:
            return self.trans_err(r, NT_NAME_COLLISION)
        self.send_trans2(r, b"", b"", [0x0000])

    def t2_set_path(self, r, params, data):
        level = struct.unpack("<H", params[0:2])[0]
        path = data.split(b"\x00", 1)[0]
        try:
            os.makedirs(self.path_of(r, path), exist_ok=True)
        except OSError:
            pass
        self.send_trans2(r, struct.pack("<H", level), b"", [0x0006])

    def t2_set_file(self, r, params, data):
        self.send_trans2(r, b"", b"", [0x000C])

    def cmd_a2(self, r):
        wc = r["wc"]
        d = r["raw"]
        w = r["words"]
        if wc >= 21:
            path_off = w[18] | (w[19] << 16)
            path_len = w[20]
            raw_path = d[path_off:path_off + path_len] if path_off else b""
        else:
            raw_path = r["body"].split(b"\x00", 1)[0]
        flags = w[2] if wc > 2 else 0
        access = (w[3] | (w[4] << 16)) if wc > 4 else 0
        disp = (w[7] | (w[8] << 16)) if wc > 8 else 1
        target = self.path_of(r, raw_path)
        log(f"  ntcreate -> {target!r} disp={disp} acc={access:x} flags={flags:x}")
        tid = self.tids.get(r["tid"], (self.root, self.share))
        base = tid[0]
        rel = os.path.relpath(target, base)
        if rel.startswith("..") and not os.path.exists(target):
            return self.trans_err(r, NT_PATH_NOT_FOUND)
        isdir = os.path.isdir(target)
        fid = None
        if isdir:
            fid = self.fh_seq
            self.fh_seq += 1
            self.files[fid] = ("dir", None, target)
            st = os.stat(target)
            attrs = attrs_for(target, True)
            eof = 0
        else:
            want_write = bool(access & (0x2 | 0x4 | 0x10000 | 0x40000))
            exists = os.path.exists(target)
            if not exists and disp in (1, 2, 3, 5):
                if not os.path.isdir(os.path.dirname(target)):
                    return self.trans_err(r, NT_PATH_NOT_FOUND)
                try:
                    open(target, "ab").close()
                    exists = True
                except OSError:
                    return self.trans_err(r, NT_ACCESS_DENIED)
            elif not exists:
                return self.trans_err(r, NT_NAME_NOT_FOUND)
            try:
                if want_write and disp in (2, 5):
                    f = open(target, "wb", 0)
                elif want_write and disp in (1, 3):
                    f = open(target, "ab", 0)
                else:
                    f = open(target, "rb")
            except OSError:
                return self.trans_err(r, NT_ACCESS_DENIED)
            if disp in (2, 5):
                f.truncate(0)
            fid = self.fh_seq
            self.fh_seq += 1
            self.files[fid] = ("file", f, target)
            st = os.stat(target)
            attrs = attrs_for(target, False)
            eof = st.st_size
        created = 1 if (not isdir and disp in (0, 2, 5)) or (isdir and disp in (0, 1)) else 3
        if r["words"] and (r["words"][0] & 0xFF) == 0:
            params = b"\xff\x00" + struct.pack("<H", fid) + b"\x00" + struct.pack("<H", fid)
            self.send(self.reply(r, [], params=params))
            return
        ct = ft(st.st_ctime)
        at = ft(st.st_atime)
        wt = ft(st.st_mtime)
        words = [0x00, fid]
        words += list(struct.unpack("<4H", ct))
        words += list(struct.unpack("<4H", at))
        words += list(struct.unpack("<4H", wt))
        words += list(struct.unpack("<4H", wt))
        words += list(struct.unpack("<4H", struct.pack("<Q", ((eof + 4095) // 4096) * 4096)))
        words += list(struct.unpack("<4H", struct.pack("<Q", eof)))
        words += [0, 0, created & 0xFFFF, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]
        self.send(self.reply(r, words[:34], b""))

    @staticmethod
    def dos_match(name, mask):
        name = name.upper()
        mask = mask.upper()
        if "." in mask:
            mstem, mext = mask.rsplit(".", 1)
        else:
            mstem, mext = mask, "*"
        if "." in name:
            nstem, next_ = name.rsplit(".", 1)
        else:
            nstem, next_ = name, ""

        # DOS 8.3 semantics: both sides are fixed-width (8/3) space-padded
        # fields; '*' in the mask expands to fill the rest of the field with
        # '?' (never with spaces, or fnmatch("INI", "*  ") fails).
        def expand(p, w):
            if "*" in p:
                head = p[: p.index("*")]
                p = head[:w] + "?" * max(0, w - len(head))
            return p[:w].ljust(w)

        return (fnmatch.fnmatch(nstem[:8].ljust(8), expand(mstem, 8))
                and fnmatch.fnmatch(next_[:3].ljust(3), expand(mext, 3)))

    def cmd_81(self, r):
        w = r["words"]
        maxcnt = w[0] if len(w) > 0 else 20
        attrs = w[1] if len(w) > 1 else 0
        raw = r["body"]
        key = ("s81", r["tid"])
        phead = raw[1:] if raw[0:1] == b"\x04" else raw
        nul = phead.find(b"\x00")
        if nul >= 0:
            phead = phead[:nul]
        is_new = bool(phead) and all(0x20 <= c < 0x7F for c in phead)
        if key in self.searches and not is_new:
            names, cursor, target = self.searches[key]
            start = cursor
            log(f"  search resume cursor={cursor} attrs={attrs:#x}")
        else:
            praw = raw
            if praw.startswith(b"\x04"):
                praw = praw[1:]
            pat = self.decode_string(praw, r["flags2"]).replace("/", "\\")
            dpart, _, mask = pat.rpartition("\\")
            mask = mask.upper() or "*"
            target = self._join_root(r, dpart)
            log(f"  search new attrs={attrs:#x} mask={mask!r} pat={pat!r} dpart={dpart!r} target={target!r}")
            if attrs & 0x08 and not (attrs & 0x37):
                self.searches[key] = ([], 0, target)
                self.send(self.reply(r, [0], b"\x05\x00\x00"))
                return
            try:
                allnames = sorted(os.listdir(target))
            except OSError as e:
                log(f"  search listdir failed: {e}")
                return self.send(self.reply(r, [], status=NT_PATH_NOT_FOUND))
            if mask in ("*", "*.*", "?"):
                names = allnames
            else:
                names = [n for n in allnames if self.dos_match(n, mask)]
            start = 0
            self.searches[key] = (names, 0, target)
        buf = b""
        count = 0
        i = start
        while i < len(names) and count < maxcnt and len(buf) + 43 <= 2048:
            full = os.path.join(target, names[i])
            try:
                st = os.stat(full)
            except OSError:
                i += 1
                continue
            isdir = os.path.isdir(full)
            base = names[i].upper()
            if "." in base:
                stem, ext = base.rsplit(".", 1)
            else:
                stem, ext = base, ""
            eattr = attrs_for(full, isdir)
            long16 = (stem[:8] + ("." + ext[:3] if ext else "")).encode(
                "cp437", "replace").ljust(16, b"\x00")
            dos11 = stem[:8].encode("cp437", "replace").ljust(8, b" ")
            dos11 += ext[:3].encode("cp437", "replace").ljust(3, b" ")
            entry = b"\x04" + struct.pack("<I", i + 1) + long16
            entry += bytes([eattr]) + struct.pack("<H", dos_time(st.st_mtime))
            entry += struct.pack("<H", dos_date(st.st_mtime)) + struct.pack("<I", 0 if isdir else st.st_size)
            entry += dos11 + bytes([eattr]) + b"\x00"
            buf += entry
            count += 1
            i += 1
        self.searches[key] = (names, i, target)
        log(f"  search start={start} count={count} total={len(names)}")
        self.send(self.reply(r, [count], b"\x05" + struct.pack("<H", len(buf)) + buf))

    def cmd_34(self, r):
        for k in [k for k in self.searches if isinstance(k, tuple) and k[0] == "s81"]:
            self.searches.pop(k, None)
        self.send(self.reply(r, [0], b""))

    def cmd_80(self, r):
        import shutil
        try:
            total, used, free = shutil.disk_usage(self.cur_root(r))
        except OSError:
            total, free = 100 * 1024 * 1024, 50 * 1024 * 1024
        total_u = min(total // 4096, 65000)
        free_u = min(free // 4096, 65000)
        log(f"  query disk total={total_u} free={free_u}")
        self.send(self.reply(r, [total_u, 8, 512, free_u, 0], b""))

    def cmd_10(self, r):
        body = r["body"]
        if body.startswith(b"\x04"):
            body = body[1:]
        path = self.decode_string(body, r["flags2"])
        target = self.path_of(r, path.encode("utf-16-le") if r["flags2"] & 0x8000 else path.encode("cp437", "replace"))
        log(f"  checkdir path={path!r}")
        if os.path.isdir(target):
            self.send(self.reply(r, []))
        else:
            self.send(self.reply(r, [], status=NT_PATH_NOT_FOUND))

    def cmd_08(self, r):
        raw = r["body"]
        if raw.startswith(b"\x04"):
            raw = raw[1:]
        target = self.path_of(r, raw)
        log(f"  query_info target={target}")
        if not os.path.exists(target) or os.path.isdir(target):
            return self.send(self.reply(r, [], status=NT_NAME_NOT_FOUND))
        st = os.stat(target)
        ut = int(st.st_mtime)
        words = [attrs_for(target, False), ut & 0xFFFF, (ut >> 16) & 0xFFFF,
                 st.st_size & 0xFFFF, (st.st_size >> 16) & 0xFFFF,
                 0, 0, 0, 0, 0]
        self.send(self.reply(r, words[:10], b""))

    def cmd_0b(self, r):
        w = r["words"]
        fid = w[0] if len(w) > 0 else 0
        count = w[1] if len(w) > 1 else 0
        off = (w[2] | (w[3] << 16)) if len(w) > 3 else 0
        data = r["body"][-count:] if count else b""
        kind, f, target = self.files.get(fid, (None, None, None))
        if kind == "file" and f is not None:
            try:
                if count == 0:
                    # DOS AH=40h with CX=0 truncates the file at the current
                    # offset; CRT fopen("wb") uses this after an OPEN_ANDX
                    # that carried no create/truncate disposition.
                    f.truncate(off)
                else:
                    f.seek(off)
                    f.write(data)
            except OSError:
                pass
        log(f"  legacy write {len(data)} bytes at {off} fid={fid}")
        self.send(self.reply(r, [len(data)], b""))

    def cmd_0a(self, r):
        w = r["words"]
        fid = w[0] if len(w) > 0 else 0
        count = w[1] if len(w) > 1 else 0
        off = (w[2] | (w[3] << 16)) if len(w) > 3 else 0
        kind, f, target = self.files.get(fid, (None, None, None))
        if kind != "file" or f is None:
            return self.send(self.reply(r, [], status=NT_NO_SUCH_FILE))
        try:
            f.seek(off)
            data = f.read(count)
        except OSError:
            return self.send(self.reply(r, [], status=NT_ACCESS_DENIED))
        log(f"  core read {len(data)} bytes at {off} fid={fid}")
        # MS LAN Manager DOS redirector parses the core READ response as
        # 6 words (WordCount=6) + ByteCount + 1 pad byte + Data. A spec
        # 5-word response without padding shifts all file data by 3 bytes.
        self.send(self.reply(r, [len(data), 0, 0, 0, 0, 0], b"\x00" + data))

    def cmd_04(self, r):
        fid = r["words"][0] if r["wc"] else 0
        e = self.files.pop(fid, None)
        if e and e[0] == "file" and e[1]:
            try:
                e[1].close()
            except OSError:
                pass
        self.send(self.reply(r, [0x0000]))

    def cmd_2d(self, r):
        w = r["words"]
        raw_path = r["body"]
        if raw_path.startswith(b"\x00"):
            raw_path = raw_path[1:]
        path = self.decode_string(raw_path, r["flags2"])
        target = self.path_of(r, path.encode("utf-16-le") if r["flags2"] & 0x8000 else path.encode("cp437", "replace"))
        access = w[3] if len(w) > 3 else 0
        open_mode = w[8] if len(w) > 8 else 0
        log(f"  open_andx path={path!r} target={target!r} access={access:#x} open_mode={open_mode:#x}")
        try:
            writable = bool(access & 0x0003) or bool(open_mode & 0x0002)
            if not os.path.exists(target):
                if not writable:
                    return self.send(self.reply(r, [], status=NT_NAME_NOT_FOUND))
                open(target, "ab").close()
            f = open(target, "r+b" if writable else "rb")
            if open_mode & 0x0002:
                f.truncate(0)
        except OSError:
            return self.send(self.reply(r, [], status=NT_NAME_NOT_FOUND))
        fid = self.fh_seq
        self.fh_seq += 1
        self.files[fid] = ("file", f, target)
        st = os.stat(target)
        if w[2] & 0x01:
            dt = int(st.st_mtime)
            params = struct.pack("<BBHHHIIHHHHHHH", 0xFF, 0, 0, fid, attrs_for(target, False),
                                 dt, st.st_size & 0xFFFFFFFF, access & 0xFFFF, 0, 1, 0, 0, 0, 0)
        else:
            params = struct.pack("<BBHHHIIHHHHHHH", 0xFF, 0, 0, fid, 0,
                                 0, 0, 0, 0, 0, 0, 0, 0, 0)
        self.send(self.reply(r, [], params=params))

    def cmd_06(self, r):
        raw = r["body"]
        if raw.startswith(b"\x04"):
            raw = raw[1:]
        target = self.path_of(r, raw)
        try:
            os.remove(target)
        except OSError:
            return self.send(self.reply(r, [], status=NT_NAME_NOT_FOUND))
        self.send(self.reply(r, []))

    def cmd_2f(self, r):
        w = r["words"]
        fid = w[2] if len(w) > 2 else 0
        count = w[10] if len(w) > 10 else 0
        off = ((w[3] | (w[4] << 16)) if len(w) > 4 else 0)
        if len(w) > 13:
            off |= (w[12] | (w[13] << 16)) << 32
        data_offset = w[11] if len(w) > 11 else 0
        body_start = 33 + 2 * r["wc"] + 2
        start = max(0, data_offset - body_start)
        data = r["body"][start:start + count]
        if data.startswith(b"\x00") and len(data) > count:
            data = data[1:1 + count]
        kind, f, target = self.files.get(fid, (None, None, None))
        if kind == "file" and f is not None:
            try:
                f.seek(off)
                f.write(data)
            except OSError:
                pass
        params = struct.pack("<BBHHHHH", 0xFF, 0, 0, len(data), 0, len(data) >> 16, 0)
        self.send(self.reply(r, [], params=params))
        log(f"  write {len(data)} bytes at {off}")

    def cmd_2e(self, r):
        w = r["words"]
        fid = w[2] if len(w) > 2 else 0
        maxc = w[5] if len(w) > 5 else 0
        off = (w[3] | (w[4] << 16)) if len(w) > 4 else 0
        if len(w) > 11:
            off |= (w[10] | (w[11] << 16)) << 32
        kind, f, target = self.files.get(fid, (None, None, None))
        if kind == "dir":
            return self.send(self.reply(r, [], status=0xC00000BA))
        try:
            f.seek(off)
            data = f.read(maxc)
        except Exception:
            return self.send(self.reply(r, [], status=0xC0000005))
        words = [0x00FF, 0, 0, 0, 63, len(data), 63, 0, len(data), 0, 0, 0, 63, 0]
        self.send(self.reply(r, words, b"\x00" * 4 + data))
        log(f"  read {len(data)} bytes at {off}")

    def cmd_2b(self, r):
        cnt = r["words"][0] if r["wc"] else 1
        data = r["body"][2:2 + 2 * cnt] if len(r["body"]) >= 2 else b""
        self.send(self.reply(r, [cnt], b"\x00\x00" + data))

    def cmd_32(self, r):
        self.send(self.reply(r, [0xFF, 0, 0x0024, 0]))

    def cmd_d4(self, r):
        self.send(self.reply(r, [0xFF, 0, 0x0024, 0]))

    def cmd_00(self, r):
        raw = r["body"]
        if raw.startswith(b"\x04"):
            raw = raw[1:]
            target = self.path_of(r, raw)
            try:
                os.mkdir(target)
            except FileExistsError:
                return self.send(self.reply(r, [], status=NT_NAME_COLLISION))
            except OSError:
                return self.send(self.reply(r, [], status=NT_ACCESS_DENIED))
            return self.send(self.reply(r, []))
        self.closed = True
        self.send(self.reply(r, []))

    def cmd_01(self, r):
        raw = r["body"]
        if raw.startswith(b"\x04"):
            raw = raw[1:]
        target = self.path_of(r, raw)
        try:
            os.rmdir(target)
        except OSError:
            return self.send(self.reply(r, [], status=NT_ACCESS_DENIED))
        self.send(self.reply(r, []))

    def cmd_02(self, r):
        raw = r["body"]
        if raw.startswith(b"\x04"):
            raw = raw[1:]
        target = self.path_of(r, raw)
        if os.path.isdir(target):
            fid = self.fh_seq
            self.fh_seq += 1
            self.files[fid] = ("dir", None, target)
            self.send(self.reply(r, [fid, dos_time(os.path.getmtime(target)), dos_date(os.path.getmtime(target)), 4096, 0x0000]))
        else:
            try:
                f = open(target, "rb")
            except OSError:
                return self.send(self.reply(r, [], status=NT_NAME_NOT_FOUND))
            fid = self.fh_seq
            self.fh_seq += 1
            self.files[fid] = ("file", f, target)
            st = os.stat(target)
            self.send(self.reply(r, [fid, dos_time(st.st_mtime), dos_date(st.st_mtime), max(st.st_size, 1) & 0xFFFF, (st.st_size >> 16) & 0xFFFF, 0x20]))

    def cmd_05(self, r):
        fid = r["words"][0]
        kind, f, target = self.files.get(fid, (None, None, None))
        if f:
            # wc=1 degenerate SEEK (just FID) from DOS redirector EOF probes — treat as offset 0
            if len(r["words"]) >= 3:
                pos = f.seek(r["words"][1] | (r["words"][2] << 16), 0 if len(r["words"]) < 4 else r["words"][3])
            else:
                pos = f.seek(0, 1)
            self.send(self.reply(r, [pos & 0xFFFF, (pos >> 16) & 0xFFFF]))
        else:
            self.send(self.reply(r, [0, 0]))


class StdioSocket:
    def __init__(self):
        self.input = sys.stdin.buffer
        self.output = sys.stdout.buffer

    def recv(self, n):
        return self.input.read(n)

    def sendall(self, data):
        self.output.write(data)
        self.output.flush()

    def close(self):
        pass


def main():
    global LOG_PATH, QUIET_STDOUT
    ap = argparse.ArgumentParser(description="Minimal SMB1 server exposing a host directory (for legacy DOS guests via QEMU slirp guestfwd).")
    ap.add_argument("--dir", required=True)
    ap.add_argument("--name", default="HOST")
    ap.add_argument("--port", type=int, default=4455)
    ap.add_argument("--log", default="smb_host.log")
    ap.add_argument("--stdio", action="store_true")
    a = ap.parse_args()
    LOG_PATH = a.log
    root = os.path.abspath(a.dir)
    if a.stdio:
        QUIET_STDOUT = True
        SmbConn(StdioSocket(), ("guestfwd", 0), root, a.name).run()
        return
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", a.port))
    srv.listen(8)
    log(f"smb_host serving {root} as \\\\SMBSERVER\\{a.name} on 127.0.0.1:{a.port}")
    while True:
        try:
            c, addr = srv.accept()
        except OSError as e:
            log(f"accept failed ({e}); continuing")
            time.sleep(0.1)
            continue
        SmbConn(c, addr, root, a.name).start()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        try:
            with open(LOG_PATH or "smb_host.log", "a") as f:
                f.write(traceback.format_exc() + "\n")
        except OSError:
            pass
        raise
