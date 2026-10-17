"""Opens Guardian Monitor (Windows). The desktop shortcut runs this with pythonw, so no console shows.
If Guardian Monitor is already open it brings its window up; if an older version is still running,
it closes that one and starts the new one."""
import json
import os
import subprocess
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = 8765
DATA_DIR = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "QueueWatcher")
local = urllib.request.build_opener(urllib.request.ProxyHandler({}))
IS_WIN = sys.platform == "win32"


def ping():
    try:
        with local.open(f"http://127.0.0.1:{PORT}/ping", timeout=1) as r:
            return r.read().decode().strip()
    except Exception:
        return ""


def stop_old():
    try:
        key = open(os.path.join(DATA_DIR, "window.key")).read().strip()
        req = urllib.request.Request(f"http://127.0.0.1:{PORT}/quit_local", data=b"", headers={"X-QW-Key": key})
        local.open(req, timeout=3)
    except Exception:
        pass
    for _ in range(20):
        if not ping():
            return
        time.sleep(0.25)
    if IS_WIN:      # still there: end it the hard way
        subprocess.run(["powershell.exe", "-NoProfile", "-Command",
                        "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*watcher_app.py*' } | "
                        "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"],
                       capture_output=True, timeout=30, creationflags=0x08000000)


def main():
    version = open(os.path.join(HERE, "app_version")).read().strip()
    running = ping()
    if running and running != f"ok {version}":
        stop_old()
        running = ""
    if running:
        try:      # bring the open window to the front
            with local.open("http://127.0.0.1:9224/json/list", timeout=2) as r:
                page = next(t for t in json.loads(r.read()) if t.get("type") == "page"
                            and t.get("url", "").startswith(f"http://127.0.0.1:{PORT}"))
            local.open(f"http://127.0.0.1:9224/json/activate/{page['id']}", timeout=2).read()
        except Exception:
            if IS_WIN:
                os.startfile(f"http://127.0.0.1:{PORT}/")
        return
    os.makedirs(DATA_DIR, exist_ok=True)
    logf = open(os.path.join(DATA_DIR, "app.log"), "a")
    flags = (0x00000008 | 0x00000200 | 0x08000000) if IS_WIN else 0   # detached, own group, no console
    subprocess.Popen([sys.executable, os.path.join(HERE, "watcher_app.py")], cwd=HERE, stdin=subprocess.DEVNULL,
                     stdout=logf, stderr=logf, creationflags=flags, close_fds=True,
                     **({} if IS_WIN else {"start_new_session": True}))


if __name__ == "__main__":
    main()
