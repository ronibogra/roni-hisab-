"""Laila Group IT Agent.

A small program that runs on a company computer and reports, to the Laila Group
Network Monitor, this computer's hardware details and live performance
(CPU, memory, disk, uptime). It shows a tray icon so the user can see it is
running, and what it does.

It sends ONLY system information. It never reads browsing data, keystrokes,
files or the screen.

Run:   python lailagent.py
Config: lailagent.ini next to this file (created on first run), or environment
        variables NETMON_SERVER and NETMON_TOKEN.
"""
import collections
import configparser
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
        while not self.stop.is_set():
            try:
                self.cycle()
            except Exception as e:
                self.last_error = str(e)
                print("Could not reach the monitor (%s). Will retry." % e)
            self.stop.wait(self.interval)


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
