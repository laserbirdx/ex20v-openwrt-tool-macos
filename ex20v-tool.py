#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TP-Link EX20v v2 - OpenWrt yedekleme / kurulum / geri yukleme araci (macOS)

Windows PowerShell araci "ex20v-tool.ps1" temel alinarak macOS icin
yeniden yazildi. Cihaz tarafinda calisan POSIX shell ajani orijinaliyle
bire bir aynidir (agent.sh). Yalnizca host (bilgisayar) tarafi - HTTP/TFTP
dosya sunucusu ve SSH oturum yonetimi - macOS'a tasindi.

Orijinal calisma: https://github.com/yucellmustafa/tplink_ex20v_openwrt_tool
Bu port, atif korunarak hazirlanmistir.
"""

import os
import re
import sys
import time
import socket
import hashlib
import threading
import http.server
import socketserver

try:
    import pexpect
except ImportError:  # pragma: no cover
    sys.stderr.write("HATA: 'pexpect' modulu gerekli (derlemede gomulu olmali).\n")
    sys.exit(1)

# --------------------------------------------------------------------------
# Sabitler (ex20v-tool.ps1 ile ayni)
# --------------------------------------------------------------------------
DEFAULT_PORT = 8000
STOCK_USER = "root"
STOCK_PASSWORD = "Root2025."
IMAGE_NAMES = [
    "ex20v-rescue.bin",
    "ex20v-kernel.bin",
    "ex20v-ubi.bin",
    "ex20v-v2-sysupgrade.bin",
]
# HTTP GET ile servis edilmesine izin verilen dosyalar
HTTP_ALLOWED = {
    "md5.txt", "reserve_md5.txt", "mtd4_kernel.bin", "mtd5_rootfs.bin",
    "mtd7_kernel_2.bin", "mtd8_rootfs_2.bin", "ex20v-rescue.bin",
    "ex20v-kernel.bin", "ex20v-ubi.bin", "ex20v-v2-sysupgrade.bin",
}
RESERVE_RE = re.compile(r"^reserve_[0-9]{2}\.bin$")
NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")

PROMPT = "EX20V_PROMPT_7f3> "          # uzaktaki shell icin pinlenen istem
PORT = DEFAULT_PORT                     # calisma aninda guncellenir

# ANSI escape temizleyici (terminal ciktisini okurken)
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


# --------------------------------------------------------------------------
# Konsol yardimcilari
# --------------------------------------------------------------------------
class C:
    CYAN = "\033[36m"; DCYAN = "\033[2;36m"; GREEN = "\033[32m"
    YELLOW = "\033[33m"; RED = "\033[31m"; GRAY = "\033[2;37m"; R = "\033[0m"


def section(text):
    print("\n" + C.DCYAN + "=" * 68 + C.R)
    print(C.CYAN + "  " + text + C.R)
    print(C.DCYAN + "=" * 68 + C.R)


def ok(text):    print(C.GREEN + "[OK] " + text + C.R)
def warn(text):  print(C.YELLOW + "[!]  " + text + C.R)
def err(text):   print(C.RED + "HATA: " + text + C.R)
def gray(text):  print(C.GRAY + text + C.R)


def read_default(prompt, default):
    try:
        value = input("%s [%s]: " % (prompt, default)).strip()
    except EOFError:
        return default
    return value if value else default


def confirm_word(message, word):
    warn(message)
    try:
        return input("Devam etmek icin %s yazin: " % word) == word
    except EOFError:
        return False


# --------------------------------------------------------------------------
# Yol / ortam cozumleme
# --------------------------------------------------------------------------
def tool_root():
    """Calistirilabilir dosyanin (veya scriptin) bulundugu dizin."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def resource_path(name):
    """Gomulu veri dosyasi (agent.sh) - PyInstaller _MEIPASS veya script dizini."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


ROOT = tool_root()
IMAGE_ROOT = os.path.join(ROOT, "files")
BACKUP_ROOT = os.path.join(ROOT, "Backups")
STATE_ROOT = os.path.join(BACKUP_ROOT, ".state")


def load_agent():
    with open(resource_path("agent.sh"), "rb") as fh:
        return fh.read()


AGENT_BYTES = None  # main() de yuklenir


# --------------------------------------------------------------------------
# Yardimci: dosya baslik/hash
# --------------------------------------------------------------------------
def file_prefix_hex(path, count, offset=0):
    with open(path, "rb") as fh:
        fh.seek(offset)
        buf = fh.read(count)
    if len(buf) != count:
        raise RuntimeError("Dosya beklenenden kisa: %s" % path)
    return buf.hex()


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def md5_file(path):
    h = hashlib.md5()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def local_ipv4_list():
    """169.254.* disinda, loopback olmayan IPv4 adresleri."""
    found = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127.") and not ip.startswith("169.254.") and ip not in found:
                found.append(ip)
    except socket.gaierror:
        pass
    # Varsayilan rota uzerinden yerel adresi de dene
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("192.168.1.1", 9))
        ip = s.getsockname()[0]
        s.close()
        if ip and not ip.startswith("169.254.") and ip not in found:
            found.insert(0, ip)
    except OSError:
        pass
    return found


def read_ipv4(purpose, preferred=""):
    addrs = local_ipv4_list()
    if addrs:
        gray("Bilgisayardaki IPv4 adresleri: " + ", ".join(addrs))
    if not preferred:
        lan = next((a for a in addrs if a.startswith("192.168.1.")), "")
        if "192.168.1.2" in addrs:
            preferred = "192.168.1.2"
        elif lan:
            preferred = lan
        elif addrs:
            preferred = addrs[0]
        else:
            preferred = "192.168.1.2"
    while True:
        value = read_default(purpose, preferred)
        try:
            socket.inet_aton(value)
            if value.count(".") == 3:
                return value
        except OSError:
            pass
        warn("Gecerli bir IPv4 adresi girin.")


def read_ssh_target(default="root@192.168.1.1"):
    while True:
        value = read_default("SSH hedefi (kullanici@IP)", default)
        if re.match(r"^[A-Za-z0-9._-]+@[A-Za-z0-9._-]+$", value):
            return value
        warn("Ornek bicim: root@192.168.1.1")


# --------------------------------------------------------------------------
# Firmware dosyalari ve yedek butunlugu dogrulama
# --------------------------------------------------------------------------
def test_image_files(show_hashes=False):
    section("Firmware dosyalari denetleniyor")
    for name in IMAGE_NAMES:
        path = os.path.join(IMAGE_ROOT, name)
        if not os.path.isfile(path):
            raise RuntimeError("Eksik dosya: %s" % path)

    rescue = os.path.join(IMAGE_ROOT, "ex20v-rescue.bin")
    kernel = os.path.join(IMAGE_ROOT, "ex20v-kernel.bin")
    ubi = os.path.join(IMAGE_ROOT, "ex20v-ubi.bin")
    if os.path.getsize(rescue) > 6291456:
        raise RuntimeError("ex20v-rescue.bin 6 MiB sinirini asiyor.")
    if os.path.getsize(kernel) > 6291456:
        raise RuntimeError("ex20v-kernel.bin 6 MiB sinirini asiyor.")
    for path in (rescue, kernel):
        if file_prefix_hex(path, 4) != "03000003":
            raise RuntimeError("TP-Link basligi gecersiz: %s" % path)
        if file_prefix_hex(path, 4, 108) != "00200080":
            raise RuntimeError("Giris adresi gecersiz: %s" % path)
    if file_prefix_hex(ubi, 4) != "55424923":   # 'UBI#'
        raise RuntimeError("ex20v-ubi.bin UBI imaji degil.")

    for name in IMAGE_NAMES:
        path = os.path.join(IMAGE_ROOT, name)
        size = os.path.getsize(path)
        ok("%s (%s bayt)" % (name, format(size, ",d")))
        if show_hashes:
            gray("     SHA256: %s" % sha256_file(path))


def test_backup_directory(path, quiet=False):
    lists = {"md5.txt": 8, "reserve_md5.txt": 12}
    for list_name, expected_count in lists.items():
        list_path = os.path.join(path, list_name)
        if not os.path.isfile(list_path):
            raise RuntimeError("Yedek eksik: %s" % list_path)
        count = 0
        with open(list_path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if not line.strip():
                    continue
                m = re.match(r"^([0-9a-fA-F]{32})\s+\*?(.+)$", line.rstrip("\n"))
                if not m:
                    raise RuntimeError("Gecersiz MD5 kaydi: %s" % list_name)
                expected = m.group(1).lower()
                name = m.group(2).strip()
                if name != os.path.basename(name) or not NAME_RE.match(name):
                    raise RuntimeError("Gecersiz yedek dosya adi: %s" % name)
                f = os.path.join(path, name)
                if not os.path.isfile(f):
                    raise RuntimeError("Yedek parcasi eksik: %s" % name)
                if md5_file(f) != expected:
                    raise RuntimeError("Yedek parcasi bozuk: %s" % name)
                count += 1
        if count != expected_count:
            raise RuntimeError("%s kayit sayisi gecersiz: %d (beklenen %d)"
                               % (list_name, count, expected_count))

    with open(os.path.join(path, "md5.txt"), "r", encoding="utf-8", errors="replace") as fh:
        md5_text = fh.read()
    if "mtd7_kernel_2.bin" not in md5_text or "mtd8_rootfs_2.bin" not in md5_text:
        raise RuntimeError("Yedekte slot B kernel/rootfs kayitlari yok.")

    expected_sizes = {
        "mtd0_boot.bin": 262144, "mtd1_romfile.bin": 1048576,
        "mtd2_config.bin": 2097152, "mtd3_radio.bin": 262144,
        "mtd4_kernel.bin": 6291456, "mtd5_rootfs.bin": 33554432,
        "mtd7_kernel_2.bin": 6291456, "mtd8_rootfs_2.bin": 33554432,
    }
    for i in range(12):
        expected_sizes["reserve_%02d.bin" % i] = 17301504 if i == 11 else 33554432
    for name, size in expected_sizes.items():
        f = os.path.join(path, name)
        if not os.path.isfile(f):
            raise RuntimeError("Yedek parcasi eksik: %s" % name)
        if os.path.getsize(f) != size:
            raise RuntimeError("Yedek parcasi boyutu gecersiz: %s" % name)
    if not os.path.isfile(os.path.join(path, "info.txt")):
        raise RuntimeError("Yedekte info.txt yok.")
    if not quiet:
        ok("Yedek butunlugu dogrulandi: %s" % path)
    return True


def select_backup_directory():
    candidates = []
    if os.path.isdir(BACKUP_ROOT):
        for entry in sorted(os.listdir(BACKUP_ROOT)):
            full = os.path.join(BACKUP_ROOT, entry)
            if os.path.isdir(full) and os.path.isfile(os.path.join(full, "md5.txt")):
                candidates.append(full)
    candidates.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    if not candidates:
        raise RuntimeError("Kullanilabilir tam yedek yok. Once ana menuden 1. adimi calistirin.")
    print("Bulunan yedekler:")
    for i, c in enumerate(candidates):
        print("  %d) %s" % (i + 1, os.path.basename(c)))
    choice = read_default("Yedek secimi", "1")
    try:
        index = int(choice)
    except ValueError:
        raise RuntimeError("Gecersiz yedek secimi.")
    if index < 1 or index > len(candidates):
        raise RuntimeError("Gecersiz yedek secimi.")
    selected = candidates[index - 1]
    test_backup_directory(selected)
    return selected


# --------------------------------------------------------------------------
# HTTP dosya sunucusu (GET servis + PUT/POST alma)  -- ex20v-tool.ps1 kontrati
# --------------------------------------------------------------------------
class _Handler(http.server.BaseHTTPRequestHandler):
    serve_dir = None    # imaj (files) dizini
    upload_dir = None   # yedek / alinan dosyalar
    stop_flag = None
    log_lines = None

    def log_message(self, *a):   # sessiz
        pass

    def _safe_name(self):
        raw = self.path.split("?", 1)[0].lstrip("/")
        from urllib.parse import unquote
        raw = unquote(raw)
        name = os.path.basename(raw)
        if name != raw or not NAME_RE.match(name):
            return None
        return name

    def _send(self, status, length=0, ctype="text/plain"):
        self.send_response(status)
        self.send_header("Content-Length", str(length))
        self.send_header("Content-Type", ctype)
        self.send_header("Connection", "close")
        self.end_headers()

    def do_HEAD(self):
        self._handle_get(head=True)

    def do_GET(self):
        name = self._safe_name()
        if name == "__stop__":
            self._send(200)
            self.stop_flag["stop"] = True
            return
        self._handle_get(head=False)

    def _handle_get(self, head):
        name = self._safe_name()
        if name is None:
            self._send(400); return
        if name not in HTTP_ALLOWED and not RESERVE_RE.match(name):
            self._send(403); return
        path = os.path.join(self.upload_dir, name)
        if not os.path.isfile(path):
            path = os.path.join(self.serve_dir, name)
        if not os.path.isfile(path):
            self._send(404); return
        size = os.path.getsize(path)
        self._send(200, size, "application/octet-stream")
        if not head:
            with open(path, "rb") as fh:
                while True:
                    chunk = fh.read(65536)
                    if not chunk:
                        break
                    try:
                        self.wfile.write(chunk)
                    except (BrokenPipeError, ConnectionResetError):
                        return
            self.log_lines.append("GET %s" % name)

    def do_POST(self):
        self.do_PUT()

    def do_PUT(self):
        name = self._safe_name()
        if name is None:
            self._send(400); return
        clen = self.headers.get("Content-Length")
        if clen is None:
            self._send(411); return
        remaining = int(clen)
        if remaining < 0 or remaining > 536870912:
            self._send(400); return
        part = os.path.join(self.upload_dir, name + ".part")
        with open(part, "wb") as out:
            while remaining > 0:
                chunk = self.rfile.read(min(65536, remaining))
                if not chunk:
                    raise RuntimeError("Baglanti dosya tamamlanmadan kesildi.")
                out.write(chunk)
                remaining -= len(chunk)
        os.replace(part, os.path.join(self.upload_dir, name))
        self._send(201)
        self.log_lines.append("PUT %s" % name)


class _ThreadingHTTP(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class HttpFileServer:
    def __init__(self, serve_dir, upload_dir, port):
        self.log_lines = []
        self.stop_flag = {"stop": False}
        handler = type("H", (_Handler,), {
            "serve_dir": serve_dir, "upload_dir": upload_dir,
            "stop_flag": self.stop_flag, "log_lines": self.log_lines,
        })
        self.httpd = _ThreadingHTTP(("0.0.0.0", port), handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever,
                                       kwargs={"poll_interval": 0.2}, daemon=True)

    def start(self):
        self.thread.start()
        ok("Dosya sunucusu 0.0.0.0:%d adresinde hazir." % PORT)
        return self

    def stop(self):
        try:
            self.httpd.shutdown()
            self.httpd.server_close()
        except Exception:
            pass
        for line in self.log_lines:
            gray("  HTTP: " + line)


# --------------------------------------------------------------------------
# Minimal TFTP (WRQ alici) sunucusu  -- stok yedek yolu icin
#   Cihaz "tftp -p" (put/WRQ) ile dosya gonderir; biz yazariz.
# --------------------------------------------------------------------------
class TftpReceiver:
    def __init__(self, upload_dir, port):
        self.upload_dir = os.path.abspath(upload_dir)
        self.port = port
        self.log_lines = []
        self._stop = threading.Event()
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("0.0.0.0", port))
        self.sock.settimeout(0.5)
        self.thread = threading.Thread(target=self._serve, daemon=True)

    def start(self):
        self.thread.start()
        ok("Yedek sunucusu 0.0.0.0:%d/UDP adresinde hazir." % self.port)
        return self

    def _safe(self, name):
        name = os.path.basename(name)
        if not NAME_RE.match(name):
            return None
        return name

    def _serve(self):
        while not self._stop.is_set():
            try:
                data, addr = self.sock.recvfrom(1024)
            except socket.timeout:
                continue
            except OSError:
                break
            if len(data) < 2:
                continue
            opcode = int.from_bytes(data[:2], "big")
            if opcode == 2:   # WRQ
                try:
                    parts = data[2:].split(b"\x00")
                    name = self._safe(parts[0].decode("latin-1"))
                except Exception:
                    continue
                if name is None:
                    continue
                self._receive(name, addr)
            # digerleri (RRQ okuma) desteklenmez - cihaz yalnizca WRQ kullanir

    def _receive(self, name, client):
        # yeni bir efemeral soket uzerinden transferi yurut (TFTP standardi)
        xfer = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        xfer.settimeout(5.0)
        try:
            tmp = os.path.join(self.upload_dir, name + ".part")
            with open(tmp, "wb") as out:
                block = 0
                # ACK 0 gonder
                xfer.sendto(b"\x00\x04\x00\x00", client)
                expected = 1
                last_len = 512
                while True:
                    try:
                        pkt, src = xfer.recvfrom(2048)
                    except socket.timeout:
                        # son ACK'i yeniden gonder
                        xfer.sendto(b"\x00\x04" + block.to_bytes(2, "big"), client)
                        continue
                    if src[0] != client[0]:
                        continue
                    if len(pkt) < 4:
                        continue
                    op = int.from_bytes(pkt[:2], "big")
                    if op == 5:   # ERROR
                        raise RuntimeError("TFTP hata paketi")
                    if op != 3:   # DATA degil
                        continue
                    blk = int.from_bytes(pkt[2:4], "big")
                    payload = pkt[4:]
                    if blk == expected:
                        out.write(payload)
                        block = blk
                        xfer.sendto(b"\x00\x04" + blk.to_bytes(2, "big"), client)
                        expected = (expected + 1) & 0xFFFF
                        last_len = len(payload)
                        if len(payload) < 512:
                            break
                    elif blk == ((expected - 1) & 0xFFFF):
                        # tekrar - ACK yeniden
                        xfer.sendto(b"\x00\x04" + blk.to_bytes(2, "big"), client)
                    # aksi halde yoksay
            os.replace(tmp, os.path.join(self.upload_dir, name))
            self.log_lines.append("TFTP %s" % name)
        except Exception as e:
            self.log_lines.append("TFTP HATA %s: %s" % (name, e))
        finally:
            xfer.close()

    def stop(self):
        self._stop.set()
        try:
            self.sock.close()
        except Exception:
            pass
        for line in self.log_lines:
            gray("  TFTP: " + line)


# --------------------------------------------------------------------------
# SSH uzaktan ajan oturumu (pexpect PTY durum makinesi)
# --------------------------------------------------------------------------
class AuthError(Exception):
    def __init__(self, message, stage):
        super().__init__(message)
        self.stage = stage


def _known_hosts(group):
    os.makedirs(STATE_ROOT, exist_ok=True)
    kh = os.path.join(STATE_ROOT, "known_hosts_%s" % group)
    # Stok/rescue her acilista yeni anahtar uretebilir; onbellegi temizle.
    if group in ("stock", "rescue") and os.path.isfile(kh):
        os.remove(kh)
    if not os.path.isfile(kh):
        open(kh, "a").close()
    return kh


def _ssh_argv(target, group, use_password):
    kh = _known_hosts(group)
    args = [
        "-tt",
        "-o", "ConnectTimeout=12",
        "-o", "ServerAliveInterval=15",
        "-o", "LogLevel=ERROR",
        "-o", "StrictHostKeyChecking=accept-new",
        "-o", "UserKnownHostsFile=%s" % kh,
    ]
    if group == "stock":
        # Fabrika yazilimi yalnizca eski ssh-rsa host anahtari sunar.
        args += [
            "-o", "HostKeyAlgorithms=+ssh-rsa",
            "-o", "PubkeyAcceptedAlgorithms=+ssh-rsa",
            "-o", "PubkeyAuthentication=no",
        ]
    if not use_password:
        args += ["-o", "BatchMode=yes", "-o", "NumberOfPasswordPrompts=0"]
    else:
        args += [
            "-o", "BatchMode=no",
            "-o", "PasswordAuthentication=yes",
            "-o", "KbdInteractiveAuthentication=yes",
            "-o", "PreferredAuthentications=password,keyboard-interactive",
            "-o", "NumberOfPasswordPrompts=1",
        ]
    args.append(target)
    return args


def _clean(text):
    return ANSI_RE.sub("", text)


def _octal_lines(data, chunk=32):
    """Ajan baytlarini 'printf ...>>/tmp/x' satirlarina cevir (busybox octal)."""
    lines = []
    for off in range(0, len(data), chunk):
        piece = data[off:off + chunk]
        esc = "".join("\\%03o" % b for b in piece)
        lines.append("printf '%s'>>/tmp/x" % esc)
    return lines


def invoke_remote_agent_session(target, action, pc_ip, extra, group,
                                login_password, secondary_password):
    """Tek bir SSH oturumu yurutur. Hata durumunda AuthError/RuntimeError firlatir."""
    if group == "stock":
        m = re.match(r"^[A-Za-z0-9._-]+@([A-Za-z0-9._-]+)$", target)
        if not m:
            raise RuntimeError("SSH hedefi kullanici@IP biciminde olmalidir.")
        target = STOCK_USER + "@" + m.group(1)

    use_password = bool(login_password)
    argv = _ssh_argv(target, group, use_password)
    print(C.CYAN + "SSH baglantisi: %s" % target + C.R)

    child = pexpect.spawn("ssh", argv, timeout=20, encoding="utf-8",
                          codec_errors="replace", echo=False)

    remote_exit = None
    linux_confirmed = False
    try:
        # --- 1) Oturum acma / parola ---
        if use_password:
            idx = child.expect([r"(?i)password:", r"(?i)passphrase",
                                pexpect.EOF, pexpect.TIMEOUT], timeout=20)
            if idx == 0:
                child.sendline(login_password)
            elif idx >= 2:
                _raise_ssh_failure(child, "ssh")
            # parola sonrasi hemen prompt gelebilir
        # Yetkilendirme reddi erken yakalama
        # --- 2) Shell'e ulas ---
        if group == "stock":
            _reach_stock_shell(child, secondary_password)
        else:
            _reach_posix_shell(child, login_password)

        # --- 3) Shell'i pinle (PS1) + echo kapat ---
        child.sendline("stty -echo 2>/dev/null; PS1='%s'; export PS1" % PROMPT)
        child.expect_exact(PROMPT, timeout=20)
        child.expect_exact(PROMPT, timeout=20)  # taze istem
        linux_confirmed = True  # posix shell'e ulasildi

        # --- 4) Ajani /tmp/x'e aktar ---
        child.sendline("rm -f /tmp/x")
        child.expect_exact(PROMPT, timeout=20)
        for line in _octal_lines(AGENT_BYTES, chunk=32):
            child.sendline(line)
            child.expect_exact(PROMPT, timeout=20)

        # --- 5) Boyut dogrula (fail-closed guvenlik) ---
        child.sendline("wc -c < /tmp/x")
        child.expect(r"(\d+)", timeout=20)
        size_reported = int(child.match.group(1))
        child.expect_exact(PROMPT, timeout=20)
        if size_reported != len(AGENT_BYTES):
            raise RuntimeError("Ajan transferi bozuk (boyut %d != %d); islem baslatilmadi."
                               % (size_reported, len(AGENT_BYTES)))

        # --- 6) Ajani calistir ---
        agent_extra = extra if extra else "x"
        child.sendline("sh /tmp/x %s %s %d %s; echo __EX20V_RC=$?"
                       % (action, pc_ip, PORT, agent_extra))

        # Ajan ilk satirda EX20V_SHELL=LINUX yazar -> dogrula
        try:
            child.expect_exact("EX20V_SHELL=LINUX", timeout=25)
            linux_confirmed = True
        except pexpect.TIMEOUT:
            pass

        # Ciktiyi akit, cikis kodunu yakala
        while True:
            idx = child.expect([r"__EX20V_RC=(\d+)", PROMPT,
                                pexpect.EOF, pexpect.TIMEOUT], timeout=300)
            # gorunen ara ciktiyi yaz
            before = _clean(child.before)
            before = before.replace(PROMPT, "")
            if before.strip():
                sys.stdout.write(before)
                sys.stdout.flush()
            if idx == 0:
                remote_exit = int(child.match.group(1))
                break
            elif idx == 1:
                continue
            else:
                break

        # --- 7) Temizlik ---
        if remote_exit is not None:
            try:
                child.expect_exact(PROMPT, timeout=10)
            except pexpect.TIMEOUT:
                pass
            child.sendline("rm -f /tmp/x")
            try:
                child.expect_exact(PROMPT, timeout=10)
            except pexpect.TIMEOUT:
                pass
        child.sendline("exit")
        try:
            child.expect(pexpect.EOF, timeout=8)
        except pexpect.TIMEOUT:
            pass
    finally:
        if child.isalive():
            child.close(force=True)

    if remote_exit is None:
        _raise_ssh_failure(child, "ssh")
    if not linux_confirmed:
        raise RuntimeError("Linux shell dogrulanamadi; guvenlik nedeniyle islem baslatilmadi.")
    if remote_exit != 0:
        raise RuntimeError("Cihaz islemi hata ile durdu (kod %d)." % remote_exit)
    print()
    return True


def _raise_ssh_failure(child, stage):
    tail = ""
    try:
        tail = _clean((child.before or "") + (child.after if isinstance(child.after, str) else ""))
    except Exception:
        pass
    low = tail.lower()
    if ("permission denied" in low or "authentication failed" in low
            or "access denied" in low or "incorrect" in low):
        raise AuthError("SSH parolasi veya kimlik dogrulamasi reddedildi.", stage)
    raise RuntimeError("SSH baglantisi basarisiz. %s" % tail.strip()[-200:])


def _reach_posix_shell(child, login_password):
    """Rescue/ana OpenWrt: dogrudan shell istemini bekle."""
    # Olasi ikinci parola istemi (bazi kurulumlar) veya dogrudan shell
    idx = child.expect([r"[#$]\s*$", r"(?i)password:",
                        r"(?i)(permission denied|authentication failed|access denied)",
                        pexpect.EOF, pexpect.TIMEOUT], timeout=20)
    if idx == 0:
        return
    if idx == 1:
        child.sendline(login_password or "")
        child.expect([r"[#$]\s*$", pexpect.EOF, pexpect.TIMEOUT], timeout=20)
        return
    _raise_ssh_failure(child, "ssh")


def _reach_stock_shell(child, secondary_password):
    """Stok TP-Link CLI: 'shell' yaz, kullanici/parola gir, Linux shell'e gec."""
    # CLI istemi mi yoksa dogrudan shell mi?
    idx = child.expect([r"(?is)(TP-Link|COMMAND-LINE Interface).*[>#]\s*$",
                        r"[#$]\s*$",
                        pexpect.TIMEOUT, pexpect.EOF], timeout=15)
    if idx == 1:
        return  # zaten Linux shell
    if idx >= 2:
        # istem algilanamadi; yine de 'shell' dene
        pass
    gray("TP-Link komut arayuzu; shell aciliyor.")
    child.sendline("shell")
    # kullanici adi iste
    idx = child.expect([r"(?i)(user\s*name|username|login|kullanici)\s*[:>]\s*$",
                        r"[#$]\s*$", pexpect.TIMEOUT, pexpect.EOF], timeout=15)
    if idx == 0:
        child.sendline(STOCK_USER)
        p = child.expect([r"(?i)(password|passwd|parola|sifre)\s*[:>]\s*$",
                          r"[#$]\s*$", pexpect.TIMEOUT, pexpect.EOF], timeout=15)
        if p == 0:
            child.sendline(secondary_password)
            s = child.expect([r"[#$]\s*$",
                              r"(?i)(incorrect|denied|fail|hatali|yanlis)",
                              pexpect.TIMEOUT, pexpect.EOF], timeout=20)
            if s != 0:
                raise AuthError("Shell sonrasi Linux parolasi reddedildi.", "shell")
        elif p == 1:
            return
        else:
            raise AuthError("Shell sonrasi Linux girisi zaman asimina ugradi.", "shell")
    elif idx == 1:
        return
    else:
        raise AuthError("Shell sonrasi Linux girisi zaman asimina ugradi.", "shell")


def invoke_remote_agent(target, action, pc_ip="127.0.0.1", extra="", group="main"):
    """Parola isteme/yeniden deneme sarmalayicisi (ex20v-tool.ps1 Invoke-RemoteAgent)."""
    import getpass
    login_password = STOCK_PASSWORD if group == "stock" else ""
    secondary_password = STOCK_PASSWORD if group == "stock" else ""
    login_prompted = False
    shell_prompted = False
    while True:
        try:
            return invoke_remote_agent_session(
                target, action, pc_ip, extra, group,
                login_password, secondary_password)
        except AuthError as e:
            if e.stage == "ssh" and not login_prompted:
                label = {"stock": "Stok firmware SSH parolasi",
                         "rescue": "Rescue OpenWrt root parolasi"}.get(
                             group, "Ana OpenWrt root parolasi")
                warn("Parolasiz veya varsayilan SSH girisi kabul edilmedi.")
                login_password = getpass.getpass(label + ": ")
                login_prompted = True
                continue
            if e.stage == "shell" and group == "stock" and not shell_prompted:
                warn("TP-Link arayuzunden sonraki Linux shell parolasi kabul edilmedi.")
                secondary_password = getpass.getpass("Linux shell parolasi: ")
                shell_prompted = True
                continue
            raise


def invoke_reboot_prompt(target, group):
    try:
        answer = input("Cihaz simdi yeniden baslatilsin mi? (E/h): ").strip()
    except EOFError:
        answer = ""
    if answer == "" or re.match(r"^[eEyY]", answer):
        invoke_remote_agent(target, "reboot", group=group)
        ok("Yeniden baslatma komutu gonderildi.")
    else:
        warn("Yeniden baslatma yapilmadi.")


# --------------------------------------------------------------------------
# Islemler (menu eylemleri)
# --------------------------------------------------------------------------
def _reset_rescue_known_hosts():
    kh = os.path.join(STATE_ROOT, "known_hosts_rescue")
    if os.path.isfile(kh):
        os.remove(kh)


def invoke_backup():
    section("1 - Tam stok firmware yedegi")
    print("Bu islem flash bellekte hicbir degisiklik yapmaz. Yaklasik 450 MB bos alan gerekir.")
    target = read_ssh_target()
    pc_ip = read_ipv4("Routerin erisecegi bilgisayar IPv4 adresi")
    session = os.path.join(BACKUP_ROOT, time.strftime("%Y%m%d-%H%M%S"))
    os.makedirs(session, exist_ok=True)
    server = None
    try:
        server = TftpReceiver(session, PORT).start()
        invoke_remote_agent(target, "backup", pc_ip=pc_ip, group="stock")
    except Exception:
        if not os.listdir(session):
            os.rmdir(session)
            warn("Hicbir yedek dosyasi alinmadi; bos oturum klasoru kaldirildi.")
        else:
            warn("Yarim kalan dosyalar korunuyor: %s" % session)
        raise
    finally:
        if server:
            server.stop()
    test_backup_directory(session)
    ok("Tam yedek hazir: %s" % session)


def invoke_prepare_rescue():
    section("2A - Stok firmware uzerinden rescue kurulumu")
    test_image_files()
    backup = select_backup_directory()
    target = read_ssh_target()
    pc_ip = read_ipv4("Stok routerin erisecegi bilgisayar IPv4 adresi")
    if not confirm_word("Bu adim slot A kernel bolumune rescue yazar ve bootflag degerini 0 yapar.", "OPENWRT"):
        warn("Islem iptal edildi."); return
    server = None
    try:
        server = HttpFileServer(IMAGE_ROOT, backup, PORT).start()
        invoke_remote_agent(target, "prepare-rescue", pc_ip=pc_ip, group="stock")
    finally:
        if server:
            server.stop()
    ok("Rescue slot A'ya yazildi ve dogrulandi.")
    warn("Sonraki acilista PC adresini 192.168.1.2/24 yapin ve kabloyu LAN1/2/3 portuna takin.")
    invoke_reboot_prompt(target, "stock")


def invoke_install_main():
    section("2B - Rescue uzerinden ana OpenWrt kurulumu")
    test_image_files()
    backup = select_backup_directory()
    target = read_ssh_target("root@192.168.1.1")
    pc_ip = read_ipv4("Rescue sisteminin erisecegi bilgisayar IPv4 adresi", "192.168.1.2")
    if not confirm_word("Bu adim stok rootfs alanini siler ve ana OpenWrt sistemini yazar.", "SIL"):
        warn("Islem iptal edildi."); return
    server = None
    try:
        _reset_rescue_known_hosts()
        server = HttpFileServer(IMAGE_ROOT, backup, PORT).start()
        invoke_remote_agent(target, "install-main", pc_ip=pc_ip, group="rescue")
    finally:
        if server:
            server.stop()
    ok("ANA OPENWRT KURULUMU TAMAMLANDI.")
    print("Yeniden acildiginda LuCI: http://192.168.1.1")
    invoke_reboot_prompt(target, "rescue")


def show_install_menu():
    section("2 - OpenWrt kurulumu")
    gray("Kurulum iki adimlidir. Sirayi atlamayin.")
    print()
    print("  1) Adim 1/2 - Stok sistemde rescue OpenWrt'yi kur")
    gray("     Once tam yedek alinmis olmali.")
    print("  2) Adim 2/2 - Rescue sistemde ana OpenWrt'yi kur")
    gray("     Birinci adimdan sonra rescue ile acilinca kullanin.")
    print("  0) Ana menuye don")
    choice = input("Secim: ").strip()
    if choice == "1":
        invoke_prepare_rescue()
    elif choice == "2":
        invoke_install_main()
    elif choice == "0":
        return
    else:
        warn("Gecersiz secim.")


def invoke_restore_stage_one():
    section("3A - Rescue uzerinden stok firmware geri yukleme")
    print("Bu adim stok slot B'yi ve reserve_area yedegini geri yukler.")
    print("Rescue slot A korunur; bootflag yalnizca tum yazmalar dogrulanirsa degisir.")
    backup = select_backup_directory()
    target = read_ssh_target("root@192.168.1.1")
    pc_ip = read_ipv4("Rescue sisteminin erisecegi bilgisayar IPv4 adresi", "192.168.1.2")
    if not confirm_word("Bu islem slot B ve reserve_area uzerindeki OpenWrt verilerini stok yedekle degistirir.", "STOKA-DON"):
        warn("Islem iptal edildi."); return
    server = None
    try:
        _reset_rescue_known_hosts()
        server = HttpFileServer(IMAGE_ROOT, backup, PORT).start()
        invoke_remote_agent(target, "restore-stock", pc_ip=pc_ip, group="rescue")
    finally:
        if server:
            server.stop()
    ok("STOK SLOT B VE RESERVE_AREA GERI YUKLENDI.")
    warn("Rescue slot A henuz korunuyor. Stok firmware sorunsuz acilmadan ikinci asamayi calistirmayin.")
    invoke_reboot_prompt(target, "rescue")


def invoke_restore_stage_two():
    section("3B - Stok donusu tamamla (slot A)")
    print("Bu adim yalnizca stok firmware slot B'den calisirken aktif olmayan slot A'yi geri yukler.")
    backup = select_backup_directory()
    target = read_ssh_target("root@192.168.1.1")
    pc_ip = read_ipv4("Stok routerin erisecegi bilgisayar IPv4 adresi")
    if not confirm_word("Stok sistemin sorunsuz acildigini dogruladiysaniz slot A da stok yedekle degistirilecek.", "TAM-STOK"):
        warn("Islem iptal edildi."); return
    server = None
    try:
        server = HttpFileServer(IMAGE_ROOT, backup, PORT).start()
        invoke_remote_agent(target, "restore-stock-finalize", pc_ip=pc_ip, group="stock")
    finally:
        if server:
            server.stop()
    ok("TAM STOK GERI YUKLEME TAMAMLANDI; HER IKI SLOT DA STOK.")
    print("Cihaz slot B uzerinde calismaya devam eder; yeniden baslatma gerekmez.")


def show_restore_menu():
    section("3 - Stok firmware geri yukleme")
    gray("Geri donus iki adimlidir ve cihazin kendi tam yedegini kullanir.")
    print()
    print("  1) Adim 1/2 - Rescue sistemden stok firmware geri yukle")
    gray("     Slot B ve reserve_area geri gelir; rescue korunur.")
    print("  2) Adim 2/2 - Stok sistem acildiktan sonra tamamla")
    gray("     Stok slot B'den calisirken slot A da geri yuklenir.")
    print("  0) Ana menuye don")
    choice = input("Secim: ").strip()
    if choice == "1":
        invoke_restore_stage_one()
    elif choice == "2":
        invoke_restore_stage_two()
    elif choice == "0":
        return
    else:
        warn("Gecersiz secim.")


def show_slot_menu():
    section("4 - Kurtarma ve acilis slotu")
    print("  1) Durumu goster (salt okunur)")
    print("  2) Sonraki acilis: slot A / rescue")
    print("  3) Sonraki acilis: slot B / ana sistem")
    print("  0) Ana menuye don")
    choice = input("Secim: ").strip()
    if choice == "0":
        return
    if choice not in ("1", "2", "3"):
        warn("Gecersiz secim."); return
    target = read_ssh_target()
    print("  1) Stok firmware SSH")
    print("  2) Rescue OpenWrt SSH")
    print("  3) Ana OpenWrt SSH")
    system_choice = read_default("Calisan sistem", "3")
    group = {"1": "stock", "2": "rescue"}.get(system_choice, "main")
    if group == "rescue":
        _reset_rescue_known_hosts()
    if choice == "1":
        invoke_remote_agent(target, "slot-show", group=group)
        return
    slot = "0" if choice == "2" else "1"
    description = "slot A / rescue" if slot == "0" else "slot B / ana sistem"
    if not confirm_word("Bootflag %s olarak degistirilecek." % description, slot):
        warn("Islem iptal edildi."); return
    invoke_remote_agent(target, "slot-set", extra=slot, group=group)
    invoke_reboot_prompt(target, group)


def invoke_local_check():
    test_image_files(show_hashes=True)
    addrs = local_ipv4_list()
    print()
    print("Yerel IPv4: " + (", ".join(addrs) if addrs else "bulunamadi"))
    ssh_path = _which("ssh")
    if ssh_path:
        ok("ssh istemcisi bulundu: %s" % ssh_path)
    else:
        warn("ssh istemcisi bulunamadi (macOS'ta /usr/bin/ssh olmali).")
    count = 0
    if os.path.isdir(BACKUP_ROOT):
        for entry in os.listdir(BACKUP_ROOT):
            if os.path.isfile(os.path.join(BACKUP_ROOT, entry, "md5.txt")):
                count += 1
    print("Tamamlanmis yedek adayi: %d" % count)


def _which(cmd):
    for p in os.environ.get("PATH", "").split(os.pathsep):
        full = os.path.join(p, cmd)
        if os.path.isfile(full) and os.access(full, os.X_OK):
            return full
    for full in ("/usr/bin/" + cmd, "/bin/" + cmd):
        if os.path.isfile(full):
            return full
    return None


def show_main_menu():
    os.system("clear")
    print(C.DCYAN + "=" * 68 + C.R)
    print(C.CYAN + "  TP-Link EX20v v2 - OpenWrt Yonetim Araci (macOS)" + C.R)
    print(C.DCYAN + "=" * 68 + C.R)
    gray("Yedekleme, kurulum ve geri donus islemlerini tek yerden yonetir.")
    print()
    warn("Ilk kurulumdan once stok firmware uzerinde root SSH erisimi acilmis olmalidir.")
    print()
    print("  1) Stok firmware tam yedegi al")
    print("  2) OpenWrt kurulumu")
    print("  3) Stok firmware geri yukleme")
    print("  4) Kurtarma ve acilis slotu")
    print("  5) Dosyalari ve gereksinimleri kontrol et")
    print("  0) Cikis")
    print()
    gray("Ilk kurulum: 1 ile yedek alin, sonra 2 ile kuruluma devam edin.")


def main():
    global AGENT_BYTES, PORT
    AGENT_BYTES = load_agent()
    if len(sys.argv) > 1:
        try:
            p = int(sys.argv[1])
            if 1024 <= p <= 65535:
                PORT = p
        except ValueError:
            pass
    os.makedirs(BACKUP_ROOT, exist_ok=True)
    os.makedirs(STATE_ROOT, exist_ok=True)

    while True:
        show_main_menu()
        try:
            choice = input("Secim: ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if choice == "0":
            break
        try:
            if choice == "1":
                invoke_backup()
            elif choice == "2":
                show_install_menu()
            elif choice == "3":
                show_restore_menu()
            elif choice == "4":
                show_slot_menu()
            elif choice == "5":
                invoke_local_check()
            else:
                warn("Gecersiz secim.")
        except KeyboardInterrupt:
            print()
            warn("Islem kullanici tarafindan kesildi.")
        except Exception as e:
            print()
            err(str(e))
            warn("Yazma islemi basladiysa cihazi yeniden baslatmadan once hata mesajini kontrol edin.")
        print()
        try:
            input("Ana menu icin Enter")
        except EOFError:
            break


if __name__ == "__main__":
    main()
