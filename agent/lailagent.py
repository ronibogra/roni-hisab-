"""Laila Group IT Agent.

A small program that runs on a company computer and reports, to the Laila Group
Network Monitor, this computer's hardware details, performance and how long each
program is used. It shows a tray icon so the user can see it is running.

For IT support, an admin can also view or control this computer's screen. That
NEVER happens silently: while a support session is open, a banner is shown on this
screen, and (if the company turns that option on) the user must click Allow first.
Every session is written to the monitor's audit log.

Normal reporting sends ONLY system information — never browsing data, keystrokes,
files or the screen. The screen is sent only during an on-screen support session.

Run:   python lailagent.py
Config: lailagent.ini next to this file (created on first run), or environment
        variables NETMON_SERVER and NETMON_TOKEN.
"""
import collections
import configparser
import io
import json
import os
import platform
import socket
import sys
import threading
import time
import urllib.request
import uuid

try:
    import psutil
except ImportError:
    print("The psutil package is missing. Install it with:  pip install psutil requests")
    sys.exit(1)

AGENT_VER = "1.0"
# When packaged as a single .exe (PyInstaller), keep the ini next to the .exe.
if getattr(sys, "frozen", False):
    HERE = os.path.dirname(os.path.abspath(sys.executable))
else:
    HERE = os.path.dirname(os.path.abspath(__file__))
INI = os.path.join(HERE, "lailagent.ini")


def load_config():
    cfg = configparser.ConfigParser()
    cfg.read(INI)
    if "agent" not in cfg:
        cfg["agent"] = {}
    a = cfg["agent"]
    server = os.environ.get("NETMON_SERVER") or a.get("server", "")
    token = os.environ.get("NETMON_TOKEN") or a.get("token", "")
    if not server or not token:
        if not os.path.exists(INI):
            a["server"] = "http://MONITOR-PC-IP:8080"
            a["token"] = "paste-the-agent-key-from-Settings-here"
            with open(INI, "w") as f:
                cfg.write(f)
            print("First run. Edit this file and set the server address and agent key:\n  %s" % INI)
        else:
            print("Set the server address and agent key in %s" % INI)
        sys.exit(1)
    return server.rstrip("/"), token, a.get("interval", "30")


# ---------------- gathering ----------------
def addresses():
    ips, macs = set(), set()
    for name, addrs in psutil.net_if_addrs().items():
        stats = psutil.net_if_stats().get(name)
        if stats and not stats.isup:
            continue
        for a in addrs:
            if a.family == socket.AF_INET and not a.address.startswith("127."):
                ips.add(a.address)
            elif getattr(a, "family", None) == psutil.AF_LINK and a.address and a.address != "00:00:00:00:00:00":
                mac = a.address.replace(":", "-").upper()
                if mac != "00-00-00-00-00-00":
                    macs.add(mac)
    return sorted(ips), sorted(macs)


def _windows_hw():
    out = {}
    try:
        import subprocess
        NOWIN = 0x08000000
        def wmic(cls, field):
            try:
                r = subprocess.run(["wmic", cls, "get", field], capture_output=True, text=True, timeout=10, creationflags=NOWIN)
                lines = [l.strip() for l in r.stdout.splitlines() if l.strip() and l.strip().lower() != field.lower()]
                return lines[0] if lines else ""
            except Exception:
                return ""
        out["serial"] = wmic("bios", "serialnumber")
        out["manufacturer"] = wmic("computersystem", "manufacturer")
        out["model"] = wmic("computersystem", "model")
    except Exception:
        pass
    return out


def _linux_hw():
    out = {}
    for key, path in (("serial", "/sys/class/dmi/id/product_serial"),
                      ("manufacturer", "/sys/class/dmi/id/sys_vendor"),
                      ("model", "/sys/class/dmi/id/product_name")):
        try:
            with open(path) as f:
                out[key] = f.read().strip()
        except OSError:
            pass
    return out


def installed_software():
    """List installed programs. On Windows, reads the uninstall registry keys."""
    progs = {}
    if platform.system() != "Windows":
        return []
    try:
        import winreg
    except ImportError:
        return []
    roots = [(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall", winreg.KEY_WOW64_64KEY),
             (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall", winreg.KEY_WOW64_32KEY),
             (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall", 0)]
    for hive, path, flag in roots:
        try:
            key = winreg.OpenKey(hive, path, 0, winreg.KEY_READ | flag)
        except OSError:
            continue
        try:
            for i in range(winreg.QueryInfoKey(key)[0]):
                try:
                    sub = winreg.OpenKey(key, winreg.EnumKey(key, i))
                    def val(name):
                        try:
                            return winreg.QueryValueEx(sub, name)[0]
                        except OSError:
                            return None
                    name = val("DisplayName")
                    if not name or val("SystemComponent") == 1 or val("ParentKeyName"):
                        continue
                    progs[str(name)] = {"name": str(name), "version": str(val("DisplayVersion") or ""),
                                        "publisher": str(val("Publisher") or "")}
                except OSError:
                    continue
        finally:
            key.Close()
    return sorted(progs.values(), key=lambda p: p["name"].lower())


def cpu_model():
    try:
        if platform.system() == "Windows":
            return platform.processor()
        if platform.system() == "Linux":
            with open("/proc/cpuinfo") as f:
                for line in f:
                    if line.startswith("model name"):
                        return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor() or "Unknown CPU"


def gather_specs():
    hw = _windows_hw() if platform.system() == "Windows" else _linux_hw()
    disks = []
    for part in psutil.disk_partitions(all=False):
        if "cdrom" in part.opts or part.fstype == "":
            continue
        try:
            u = psutil.disk_usage(part.mountpoint)
            disks.append({"mount": part.mountpoint, "total": u.total, "free": u.free})
        except OSError:
            pass
    return {
        "os": platform.system(), "os_version": platform.platform(),
        "hostname": socket.gethostname(),
        "cpu": cpu_model(), "cores": psutil.cpu_count(logical=False), "threads": psutil.cpu_count(logical=True),
        "ram_total": psutil.virtual_memory().total,
        "disks": disks,
        "serial": hw.get("serial", ""), "manufacturer": hw.get("manufacturer", ""), "model": hw.get("model", ""),
        "boot_time": psutil.boot_time(),
        "machine_id": hex(uuid.getnode()),
    }


# ---------------- software usage (running time per program) ----------------
# A background sampler adds, every few seconds, the elapsed time to each program
# that is currently running. We report the seconds since the last report, and the
# server adds them up per program per day. This is running time only — not what is
# typed, shown on screen, or which files/sites are opened.
_apps = {"t": None, "secs": collections.defaultdict(float), "lock": threading.Lock()}
# programs that are always-on background noise, not "software people use"
_APP_SKIP = {"system idle process", "system", "registry", "memory compression", "svchost.exe",
             "runtimebroker.exe", "dwm.exe", "csrss.exe", "wininit.exe", "services.exe", "lsass.exe",
             "smss.exe", "fontdrvhost.exe", "ctfmon.exe", "sihost.exe", "taskhostw.exe", "spoolsv.exe",
             "searchindexer.exe", "wmiprvse.exe", "conhost.exe", "backgroundtaskhost.exe",
             "lailagent.exe", "pythonw.exe", "python.exe"}


def accumulate_apps():
    now = time.time()
    names = set()
    for p in psutil.process_iter(["name"]):
        n = (p.info.get("name") or "").strip()
        if n and n.lower() not in _APP_SKIP:
            names.add(n)
    with _apps["lock"]:
        if _apps["t"] is not None:
            dt = now - _apps["t"]
            if 0 < dt < 600:
                for n in names:
                    _apps["secs"][n] += dt
        _apps["t"] = now


def drain_apps():
    with _apps["lock"]:
        out = {n: round(sec) for n, sec in _apps["secs"].items() if sec >= 1}
        _apps["secs"].clear()
        return out


_last_net = {"t": None, "rx": 0, "tx": 0}


def gather_perf():
    cpu = psutil.cpu_percent(interval=None)
    vm = psutil.virtual_memory()
    try:
        disk = psutil.disk_usage(os.path.abspath(os.sep))
        disk_pct = disk.percent
    except OSError:
        disk_pct = None
    top = []
    for p in sorted(psutil.process_iter(["name", "cpu_percent", "memory_percent"]),
                    key=lambda p: p.info.get("cpu_percent") or 0, reverse=True)[:6]:
        top.append({"name": p.info.get("name") or "?", "cpu": round(p.info.get("cpu_percent") or 0, 1),
                    "mem": round(p.info.get("memory_percent") or 0, 1)})
    now = time.time()
    io = psutil.net_io_counters()
    down = up = None
    if _last_net["t"] and now > _last_net["t"]:
        sec = now - _last_net["t"]
        down = round((io.bytes_recv - _last_net["rx"]) * 8 / sec / 1e6, 2)
        up = round((io.bytes_sent - _last_net["tx"]) * 8 / sec / 1e6, 2)
    _last_net.update(t=now, rx=io.bytes_recv, tx=io.bytes_sent)
    return {"cpu": round(cpu, 1), "mem": round(vm.percent, 1), "mem_used": vm.total - vm.available,
            "disk_pct": disk_pct, "uptime": now - psutil.boot_time(), "top": top,
            "net_down": down, "net_up": up}


# ---------------- reporting ----------------
class Agent:
    def __init__(self, server, token, interval):
        self.server, self.token = server, token
        self.interval = max(10, int(interval or 30))
        self.stop = threading.Event()
        self.last_ok = None
        self.last_error = None
        self.specs = None
        self.software = None
        self._n = 0

    def post(self, path, payload):
        data = json.dumps(payload).encode()
        req = urllib.request.Request(self.server + path, data=data, method="POST",
                                     headers={"Content-Type": "application/json", "Authorization": "Bearer " + self.token})
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read().decode())

    def cycle(self):
        ips, macs = addresses()
        if self.specs is None:
            self.specs = gather_specs()
        psutil.cpu_percent(interval=None)  # prime
        time.sleep(0.4)
        payload = {"agent_ver": AGENT_VER, "hostname": socket.gethostname(), "ips": ips, "macs": macs,
                   "specs": self.specs, "perf": gather_perf(), "apps": drain_apps()}
        # installed-software list changes rarely, so send it on the first report and
        # then about once an hour, to save bandwidth across many PCs.
        if self._n % 120 == 0:
            if self.software is None:
                self.software = installed_software()
            payload["software"] = self.software
        self._n += 1
        res = self.post("/api/agent/report", payload)
        self.interval = max(10, int(res.get("interval") or self.interval))
        self.last_ok = time.time()
        self.last_error = None

    def sampler(self):
        """Every few seconds, add running time to each open program."""
        while not self.stop.is_set():
            try:
                accumulate_apps()
            except Exception:
                pass
            self.stop.wait(8)

    def run(self):
        print("Laila Group IT Agent %s -> %s" % (AGENT_VER, self.server))
        print("Reporting hardware details, performance and program running-time. "
              "No browsing, keystrokes or screen data is sent.")
        threading.Thread(target=self.sampler, daemon=True).start()
        self.remote = RemoteClient(self)
        threading.Thread(target=self.remote.loop, daemon=True).start()
        while not self.stop.is_set():
            try:
                self.cycle()
            except Exception as e:
                self.last_error = str(e)
                print("Could not reach the monitor (%s). Will retry." % e)
            self.stop.wait(self.interval)


# ---------------- remote support (view / control this screen) ----------------
IS_WINDOWS = platform.system() == "Windows"

# Map browser key names to Windows virtual-key codes for the common keys.
_VK = {
    "Enter": 0x0D, "Backspace": 0x08, "Tab": 0x09, "Escape": 0x1B, "Delete": 0x2E,
    "ArrowLeft": 0x25, "ArrowUp": 0x26, "ArrowRight": 0x27, "ArrowDown": 0x28,
    "Home": 0x24, "End": 0x23, "PageUp": 0x21, "PageDown": 0x22, "Insert": 0x2D,
    "Control": 0x11, "Shift": 0x10, "Alt": 0x12, "Meta": 0x5B, "CapsLock": 0x14,
    " ": 0x20, "F1": 0x70, "F2": 0x71, "F3": 0x72, "F4": 0x73, "F5": 0x74,
    "F6": 0x75, "F7": 0x76, "F8": 0x77, "F9": 0x78, "F10": 0x79, "F11": 0x7A, "F12": 0x7B,
}

if IS_WINDOWS:
    import ctypes
    _u32 = ctypes.windll.user32
    MOUSEEVENTF = {"move": 0x0001, "ldown": 0x0002, "lup": 0x0004, "rdown": 0x0008,
                   "rup": 0x0010, "mdown": 0x0020, "mup": 0x0040, "wheel": 0x0800}

    def screen_size():
        return _u32.GetSystemMetrics(0), _u32.GetSystemMetrics(1)

    def apply_input(ev, sw, sh):
        try:
            t = ev.get("type")
            if t in ("move", "down", "up", "click", "scroll"):
                x = int(max(0.0, min(1.0, ev.get("x", 0))) * sw)
                y = int(max(0.0, min(1.0, ev.get("y", 0))) * sh)
                _u32.SetCursorPos(x, y)
            if t == "scroll":
                _u32.mouse_event(MOUSEEVENTF["wheel"], 0, 0, int(-ev.get("dy", 0) * 120), 0)
            elif t in ("down", "up", "click"):
                btn = ev.get("button", "left")
                pre = "l" if btn == "left" else "r" if btn == "right" else "m"
                if t in ("down", "click"):
                    _u32.mouse_event(MOUSEEVENTF[pre + "down"], 0, 0, 0, 0)
                if t in ("up", "click"):
                    _u32.mouse_event(MOUSEEVENTF[pre + "up"], 0, 0, 0, 0)
            elif t == "key":
                vk = _key_vk(ev.get("key", ""))
                if vk is not None:
                    down = ev.get("action") != "up"
                    _u32.keybd_event(vk & 0xFF, 0, 0 if down else 2, 0)
        except Exception:
            pass

    def _key_vk(key):
        if key in _VK:
            return _VK[key]
        if len(key) == 1:
            r = _u32.VkKeyScanW(ord(key))
            if r != -1:
                return r & 0xFF
        return None
else:
    def screen_size():
        return 0, 0

    def apply_input(ev, sw, sh):
        pass


class RemoteUI:
    """Owns a Tk window on its own thread: a consent dialog and a 'being viewed' banner."""
    def __init__(self):
        self.q = []
        self.lock = threading.Lock()
        self.root = None
        self.banner = None
        self.consent_cb = None
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        try:
            import tkinter as tk
        except Exception:
            return
        self.tk = tk
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.after(150, self._pump)
        try:
            self.root.mainloop()
        except Exception:
            pass

    def _pump(self):
        with self.lock:
            jobs, self.q = self.q, []
        for kind, arg in jobs:
            try:
                if kind == "banner":
                    self._show_banner(arg)
                elif kind == "hide":
                    self._hide_banner()
                elif kind == "consent":
                    self._ask_consent(*arg)
            except Exception:
                pass
        if self.root:
            self.root.after(150, self._pump)

    def _show_banner(self, text):
        tk = self.tk
        if self.banner is None:
            self.banner = tk.Toplevel(self.root)
            self.banner.overrideredirect(True)
            self.banner.attributes("-topmost", True)
            try:
                self.banner.attributes("-alpha", 0.92)
            except Exception:
                pass
            sw = self.root.winfo_screenwidth()
            self.banner.geometry("%dx34+0+0" % sw)
            self.banner.configure(bg="#b00020")
            self.lbl = tk.Label(self.banner, bg="#b00020", fg="white", font=("Segoe UI", 11, "bold"))
            self.lbl.pack(fill="both", expand=True)
        self.lbl.config(text=text)
        self.banner.deiconify()

    def _hide_banner(self):
        if self.banner is not None:
            self.banner.withdraw()

    def _ask_consent(self, text, cb):
        tk = self.tk
        win = tk.Toplevel(self.root)
        win.attributes("-topmost", True)
        win.title("Laila Group IT support")
        win.geometry("460x180")
        win.configure(bg="white")
        tk.Label(win, text="Remote support request", bg="white", fg="#1f4e79",
                 font=("Segoe UI", 13, "bold")).pack(pady=(16, 4))
        tk.Label(win, text=text, bg="white", wraplength=420, justify="center",
                 font=("Segoe UI", 10)).pack(pady=4)
        row = tk.Frame(win, bg="white")
        row.pack(pady=14)

        def done(ok):
            try:
                win.destroy()
            except Exception:
                pass
            cb(ok)
        tk.Button(row, text="Allow", width=12, bg="#1f4e79", fg="white",
                  command=lambda: done(True)).pack(side="left", padx=8)
        tk.Button(row, text="Decline", width=12, command=lambda: done(False)).pack(side="left", padx=8)
        win.protocol("WM_DELETE_WINDOW", lambda: done(False))

    def show_banner(self, text):
        with self.lock:
            self.q.append(("banner", text))

    def hide_banner(self):
        with self.lock:
            self.q.append(("hide", None))

    def ask_consent(self, text, cb):
        with self.lock:
            self.q.append(("consent", (text, cb)))


class RemoteClient:
    """Polls the server for support-session commands; streams the screen and applies input."""
    def __init__(self, agent):
        self.a = agent
        self.poll = 2
        self.ui = RemoteUI() if IS_WINDOWS else None
        self.session = None
        self.streaming = False
        self.consent_asked = None

    def _post(self, path, **kw):
        import urllib.request
        url = self.a.server + path
        data = kw.get("data")
        headers = {"Authorization": "Bearer " + self.a.token}
        headers.update(kw.get("headers", {}))
        if "json" in kw:
            data = json.dumps(kw["json"]).encode()
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, method="POST", headers=headers)
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read().decode())

    def loop(self):
        import urllib.error
        while not self.a.stop.is_set():
            if self.streaming:
                self.a.stop.wait(1.0)
                continue
            try:
                ips, _ = addresses()
                cmd = self._post("/api/agent/remote/poll", json={"ips": ips})
                self._handle(cmd)
            except urllib.error.URLError:
                pass
            except Exception:
                pass
            self.a.stop.wait(self.poll)

    def _handle(self, cmd):
        action = cmd.get("action")
        ip = cmd.get("ip")
        if action == "consent":
            sid = cmd.get("session")
            if self.consent_asked != sid and self.ui:
                self.consent_asked = sid
                who = cmd.get("by", "IT")
                verb = "control" if cmd.get("mode") == "control" else "view"
                self.ui.ask_consent(
                    "%s from Laila Group IT would like to %s this computer's screen to help you.\n\n"
                    "Do you allow it?" % (who, verb),
                    lambda ok: self._consent(ip, sid, ok))
        elif action == "stream":
            self.consent_asked = None
            threading.Thread(target=self._stream, args=(ip, cmd), daemon=True).start()
        elif action in ("stop", "none"):
            self.consent_asked = None

    def _consent(self, ip, sid, ok):
        try:
            self._post("/api/agent/remote/consent", json={"ip": ip, "session": sid, "granted": bool(ok)})
        except Exception:
            pass

    def _stream(self, ip, cmd):
        if self.streaming:
            return
        self.streaming = True
        sid = cmd.get("session")
        try:
            import mss
            from PIL import Image
        except Exception:
            self.streaming = False
            print("Remote view needs the 'mss' and 'Pillow' packages.")
            return
        self._banner(cmd)
        sw, sh = screen_size()
        try:
            with mss.mss() as sct:
                mon = sct.monitors[1]
                if not sw:
                    sw, sh = mon["width"], mon["height"]
                while not self.a.stop.is_set():
                    t0 = time.time()
                    quality = int(cmd.get("quality", 55))
                    maxw = int(cmd.get("max_width", 1366))
                    shot = sct.grab(mon)
                    img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
                    if img.width > maxw:
                        img = img.resize((maxw, int(img.height * maxw / img.width)))
                    buf = io.BytesIO()
                    img.save(buf, "JPEG", quality=quality)
                    headers = {"Content-Type": "image/jpeg", "X-Screen-W": str(sw), "X-Screen-H": str(sh),
                               "X-Frame-W": str(img.width), "X-Frame-H": str(img.height)}
                    res = self._post("/api/agent/remote/frame?ip=%s&session=%s" % (ip, sid),
                                     data=buf.getvalue(), headers=headers)
                    if res.get("action") == "stop":
                        break
                    if res.get("mode") == "control":
                        for ev in res.get("input", []):
                            apply_input(ev, sw, sh)
                    # aim for ~5 frames a second
                    self.a.stop.wait(max(0.0, 0.2 - (time.time() - t0)))
        except Exception as e:
            print("Remote session ended: %s" % e)
        finally:
            self.streaming = False
            if self.ui:
                self.ui.hide_banner()

    def _banner(self, cmd):
        if not self.ui:
            return
        who = cmd.get("by", "IT")
        verb = "controlling" if cmd.get("mode") == "control" else "viewing"
        self.ui.show_banner("  ●  Laila Group IT (%s) is %s this screen for support." % (who, verb))


# ---------------- tray (optional, Windows/desktop) ----------------
def run_with_tray(agent):
    try:
        import pystray
        from PIL import Image, ImageDraw
    except ImportError:
        agent.run()
        return
    img = Image.new("RGB", (64, 64), "#1f4e79")
    d = ImageDraw.Draw(img)
    d.rectangle([14, 14, 50, 50], outline="#8ccaff", width=4)
    d.text((22, 22), "L", fill="#ffffff")

    def status(_):
        if agent.last_error:
            return "Not connected: " + agent.last_error
        if agent.last_ok:
            return "Connected. Last report %ds ago" % int(time.time() - agent.last_ok)
        return "Starting..."

    def about(icon, item):
        icon.notify("Laila Group IT Agent\nThis computer is managed by IT. The agent reports hardware "
                    "details and performance only. It does not read your browsing, files, keystrokes or screen.",
                    "Laila Group IT Agent")

    def open_monitor(icon, item):
        import webbrowser
        webbrowser.open(agent.server)

    def quit_agent(icon, item):
        agent.stop.set()
        icon.stop()

    menu = pystray.Menu(
        pystray.MenuItem("Laila Group IT Agent — managed by IT", None, enabled=False),
        pystray.MenuItem(status, None, enabled=False),
        pystray.MenuItem("What does this do?", about),
        pystray.MenuItem("Open monitor", open_monitor),
        pystray.MenuItem("Quit", quit_agent),
    )
    icon = pystray.Icon("lailagent", img, "Laila Group IT Agent — managed by IT", menu)
    threading.Thread(target=agent.run, daemon=True).start()
    icon.run()


def main():
    server, token, interval = load_config()
    agent = Agent(server, token, interval)
    if "--no-tray" in sys.argv or platform.system() not in ("Windows", "Darwin"):
        agent.run()
    else:
        run_with_tray(agent)


if __name__ == "__main__":
    main()
