"""
Guardian Monitor for Windows: the Windows parts.

The main app (watcher_app.py) is shared with the Mac version. On a Mac it controls Chrome with
AppleScript and keeps passwords in the Keychain. Windows has neither, so this file swaps those
pieces for Windows ones:

  Chrome control   Guardian Monitor's own Chrome window, controlled through Chrome's built-in
                   DevTools connection (only reachable from this PC). You sign in to Pokémon
                   Center in that window once and it stays signed in.
  Passwords        Windows Credential Manager
  Notifications    Windows notifications (bottom-right corner)
  Stay awake       Windows "don't sleep" request while watching / during a schedule
  App window       Microsoft Edge (or Chrome) in app mode: its own window, no tabs or address bar.
                   Closing it quits Guardian Monitor.
"""

import base64
import json
import os
import secrets
import socket
import struct
import subprocess
import sys
import threading
import time
import urllib.request
from urllib.parse import urlparse

IS_WIN = sys.platform == "win32"
NO_WINDOW = 0x08000000 if IS_WIN else 0          # CREATE_NO_WINDOW: never flash a black console window
NEW_GROUP = 0x00000200 if IS_WIN else 0          # CREATE_NEW_PROCESS_GROUP

CDP_PORT = int(os.environ.get("QW_CDP_PORT", "9223"))       # Guardian Monitor's Chrome
PANEL_PORT = int(os.environ.get("QW_PANEL_PORT", "9224"))   # the app window

W = None          # the main app module, set by install()
_local = urllib.request.build_opener(urllib.request.ProxyHandler({}))   # never send local calls through a proxy


# ---------------- finding Chrome / Edge ----------------
def _program_dirs():
    for k in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA", "PROGRAMW6432"):
        v = os.environ.get(k)
        if v:
            yield v


def find_chrome():
    if os.environ.get("QW_CHROME_BIN"):
        return os.environ["QW_CHROME_BIN"]
    for base in _program_dirs():
        p = os.path.join(base, "Google", "Chrome", "Application", "chrome.exe")
        if os.path.exists(p):
            return p
    return ""


def find_panel_browser():
    """Edge comes with Windows, so the app window uses it. Chrome if Edge is missing."""
    if os.environ.get("QW_PANEL_BIN"):
        return os.environ["QW_PANEL_BIN"]
    for base in _program_dirs():
        p = os.path.join(base, "Microsoft", "Edge", "Application", "msedge.exe")
        if os.path.exists(p):
            return p
    return find_chrome()


def chrome_version_major():
    """chrome.exe --version opens a browser window on Windows instead of printing, so read the
    version from Chrome's install folder (it has a folder named like 141.0.7390.66)."""
    import re
    exe = find_chrome()
    if exe:
        try:
            for name in os.listdir(os.path.dirname(exe)):
                m = re.match(r"(\d+)\.\d+\.\d+\.\d+$", name)
                if m:
                    return m.group(1)
        except Exception:
            pass
    return "141"


# ---------------- a tiny WebSocket client (for Chrome's DevTools connection) ----------------
class CDPError(Exception):
    pass


class _WS:
    def __init__(self, url, timeout=30):
        u = urlparse(url)
        self.sock = socket.create_connection((u.hostname, u.port or 80), timeout=timeout)
        key = base64.b64encode(secrets.token_bytes(16)).decode()
        path = u.path + ("?" + u.query if u.query else "")
        self.sock.sendall((f"GET {path} HTTP/1.1\r\nHost: {u.hostname}:{u.port}\r\n"
                           "Upgrade: websocket\r\nConnection: Upgrade\r\n"
                           f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise CDPError("Chrome closed the connection")
            buf += chunk
        head, self.buf = buf.split(b"\r\n\r\n", 1)
        if b" 101" not in head.split(b"\r\n", 1)[0]:
            raise CDPError("Chrome refused the connection: " + head.split(b"\r\n", 1)[0].decode("latin-1"))
        self.n = 0

    def close(self):
        try:
            self.sock.close()
        except Exception:
            pass

    def _exact(self, n):
        while len(self.buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise CDPError("Chrome closed the connection")
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def _send_frame(self, opcode, data):
        n = len(data)
        hdr = bytearray([0x80 | opcode])
        if n < 126:
            hdr.append(0x80 | n)
        elif n < 65536:
            hdr.append(0x80 | 126)
            hdr += struct.pack(">H", n)
        else:
            hdr.append(0x80 | 127)
            hdr += struct.pack(">Q", n)
        mask = secrets.token_bytes(4)
        # XOR the whole payload with the repeating 4-byte mask (fast, via big integers)
        reps = (mask * (n // 4 + 1))[:n]
        masked = (int.from_bytes(data, "big") ^ int.from_bytes(reps, "big")).to_bytes(n, "big") if n else b""
        self.sock.sendall(bytes(hdr) + mask + masked)

    def recv(self):
        parts = []
        while True:
            b1, b2 = self._exact(2)
            fin, op, n = b1 & 0x80, b1 & 0x0F, b2 & 0x7F
            if n == 126:
                n = struct.unpack(">H", self._exact(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._exact(8))[0]
            mask = self._exact(4) if b2 & 0x80 else None
            data = self._exact(n)
            if mask:
                data = bytes(c ^ mask[i % 4] for i, c in enumerate(data))
            if op == 8:
                raise CDPError("Chrome closed the connection")
            if op == 9:
                self._send_frame(10, data)
                continue
            if op == 10:
                continue
            parts.append(data)
            if fin:
                return b"".join(parts).decode("utf-8", "replace")

    def call(self, method, params=None, timeout=30):
        self.n += 1
        my_id = self.n
        self._send_frame(1, json.dumps({"id": my_id, "method": method, "params": params or {}}).encode())
        end = time.time() + timeout
        while True:
            self.sock.settimeout(max(0.5, end - time.time()))
            msg = json.loads(self.recv())
            if msg.get("id") == my_id:
                if "error" in msg:
                    raise CDPError(msg["error"].get("message", "error"))
                return msg.get("result", {})
            if time.time() > end:
                raise CDPError("Chrome took too long to answer")


def _get_json(port, path, timeout=3):
    with _local.open(f"http://127.0.0.1:{port}{path}", timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def endpoint_up(port=None):
    try:
        _get_json(port or CDP_PORT, "/json/version", timeout=2)
        return True
    except Exception:
        return False


def browser_call(method, params=None, port=None, timeout=30):
    v = _get_json(port or CDP_PORT, "/json/version")
    ws = _WS(v["webSocketDebuggerUrl"], timeout=timeout)
    try:
        return ws.call(method, params, timeout)
    finally:
        ws.close()


def page_targets(port=None):
    try:
        return [t for t in _get_json(port or CDP_PORT, "/json/list") if t.get("type") == "page"]
    except Exception:
        return None          # Chrome isn't running


class TabGone(Exception):
    pass


def page_call(win, method, params=None, timeout=30):
    tid = win[1]
    targets = page_targets()
    if targets is None:
        raise TabGone("Guardian Monitor's Chrome window is closed")
    t = next((x for x in targets if x.get("id") == tid), None)
    if not t:
        raise TabGone("tab not found")
    if not t.get("webSocketDebuggerUrl"):
        raise CDPError("someone else is connected to this tab")
    ws = _WS(t["webSocketDebuggerUrl"], timeout=timeout)
    try:
        return ws.call(method, params, timeout)
    finally:
        ws.close()


# ---------------- Guardian Monitor's Chrome window ----------------
def profile_dir():
    return os.path.join(W.DATA_DIR, "chrome-profile")


CHROME_FLAGS = [
    "--no-first-run", "--no-default-browser-check",
    # Keep the page fully working while it's minimized (Windows otherwise slows hidden windows down).
    "--disable-background-timer-throttling", "--disable-renderer-backgrounding",
    "--disable-backgrounding-occluded-windows", "--disable-features=CalculateNativeWinOcclusion",
    "--hide-crash-restore-bubble",
]


def launch_chrome(url):
    exe = find_chrome()
    if not exe:
        return None, "Google Chrome isn't installed. Get it at google.com/chrome, then press Start again."
    os.makedirs(profile_dir(), exist_ok=True)
    cmd = [exe, f"--remote-debugging-port={CDP_PORT}", f"--user-data-dir={profile_dir()}", *CHROME_FLAGS]
    if os.environ.get("QW_HEADLESS_SANDBOX_OFF"):
        cmd.append("--no-sandbox")
    cmd.append(url)
    try:
        # Not tied to Guardian Monitor: if you quit the app, the Chrome window (and your queue spot) stays.
        subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         creationflags=NEW_GROUP, close_fds=True,
                         **({} if IS_WIN else {"start_new_session": True}))
    except Exception as e:
        return None, f"couldn't start Chrome ({e})"
    for _ in range(60):
        targets = page_targets()
        if targets:
            return targets[0]["id"], ""
        time.sleep(0.5)
    return None, "Chrome started but didn't answer"


def set_window_state(win, state):
    """'minimized' or 'normal'."""
    try:
        wid = browser_call("Browser.getWindowForTarget", {"targetId": win[1]})["windowId"]
        browser_call("Browser.setWindowBounds", {"windowId": wid, "bounds": {"windowState": state}})
        return True
    except Exception:
        return False


def _force_foreground(title):
    """Windows only lets the app in front steal focus, so give Chrome's window a firm nudge."""
    if not IS_WIN or not title:
        return
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        found = []
        proc_t = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def each(hwnd, _):
            if user32.IsWindowVisible(hwnd):
                n = user32.GetWindowTextLengthW(hwnd)
                if n:
                    buf = ctypes.create_unicode_buffer(n + 1)
                    user32.GetWindowTextW(hwnd, buf, n + 1)
                    cls = ctypes.create_unicode_buffer(64)
                    user32.GetClassNameW(hwnd, cls, 64)
                    if cls.value.startswith("Chrome_WidgetWin") and buf.value.startswith(title[:60]):
                        found.append(hwnd)
            return True

        user32.EnumWindows(proc_t(each), 0)
        for hwnd in found[:1]:
            user32.ShowWindow(hwnd, 9)                       # restore
            if not user32.SetForegroundWindow(hwnd):
                user32.keybd_event(0x12, 0, 0, 0)            # tapping Alt lets an app take focus
                user32.keybd_event(0x12, 0, 2, 0)
                user32.SetForegroundWindow(hwnd)
    except Exception:
        pass


# ---------------- replacements for the Mac Chrome functions ----------------
def chrome_running():
    # On Windows the watcher uses its own Chrome window, so "Chrome closed" is handled as
    # "the Pokémon Center window was closed" (it stops and tells you) instead of pausing.
    return True


def tab_do(win, action, timeout=20):
    return "", "not used on Windows"


def tab_get(win, action, timeout=20):
    return "", "not used on Windows"


def find_tab(url):
    host = W.site_host(url)
    targets = page_targets()
    if not host or not targets:
        return None
    for t in targets:
        u = t.get("url", "")
        if host in W.site_host(u) and not any(p in u.lower() for p in W.BUSY_PATHS):
            return ("cdp", t["id"])
    return None


def new_window(url, minimized):
    """A new window in Guardian Monitor's Chrome (starting Chrome if needed)."""
    if page_targets() is None:
        tid, err = launch_chrome(url)
        if not tid:
            return None, err
        win = ("cdp", tid)
    else:
        try:
            win = ("cdp", browser_call("Target.createTarget", {"url": url, "newWindow": True})["targetId"])
        except Exception as e:
            return None, f"couldn't open a window ({e})"
    if minimized:
        set_window_state(win, "minimized")
    return win, ""


def open_window(url):
    found = find_tab(url)
    if found:
        W.log("Found an open tab on this site in Guardian Monitor's Chrome. Using it.")
        return found, ""
    stay_back = W.load_settings().get("pkc_background", True)
    win, err = new_window(url, stay_back)
    if win:
        W.log("Opened the site in Guardian Monitor's Chrome window" + (" (minimized)." if stay_back else "."))
        if not stay_back:
            bring_to_front(win)
    return win, err


def window_exists(win):
    if not win:
        return False
    targets = page_targets()
    if targets is None:
        return False                      # the Chrome window was closed
    return any(t.get("id") == win[1] for t in targets)


def reload_tab(win):
    try:
        page_call(win, "Page.reload", {"ignoreCache": False})
    except Exception:
        pass


def close_tab(win):
    try:
        browser_call("Target.closeTarget", {"targetId": win[1]}, timeout=10)
    except Exception:
        pass


def go_to(win, url):
    try:
        page_call(win, "Page.navigate", {"url": url})
    except Exception:
        pass


def _eval(win, js, timeout=30):
    r = page_call(win, "Runtime.evaluate", {"expression": js, "returnByValue": True}, timeout=timeout)
    if r.get("exceptionDetails"):
        d = r["exceptionDetails"]
        raise CDPError((d.get("exception") or {}).get("description") or d.get("text") or "script error")
    v = (r.get("result") or {}).get("value")
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    return v if isinstance(v, str) else json.dumps(v)


def run_js(win, js):
    try:
        return _eval(win, js), ""
    except TabGone as e:
        return "", str(e)
    except Exception as e:
        return "", f"Chrome couldn't run it ({e})"


def wait_for_load(win, stop, min_wait=3, max_wait=25):
    if stop.wait(min_wait):
        return
    waited = min_wait
    while waited < max_wait:
        try:
            if _eval(win, "document.readyState", timeout=10) == "complete":
                break
        except TabGone:
            break
        except Exception:
            pass
        if stop.wait(1):
            return
        waited += 1
    stop.wait(1)


def read_page(win):
    js = "JSON.stringify([location.href, document.title, " + W.PAGE_JS + "])"
    try:
        url, title, text = json.loads(_eval(win, js))
        W.last_js_error["text"] = ""
        return url, title, text, False, False
    except TabGone as e:
        W.last_js_error["text"] = str(e)
        return "", "", "", True, True
    except Exception as e:
        # The page may be in the middle of loading. Fall back to Chrome's own list of tabs.
        W.last_js_error["text"] = str(e)
        t = next((x for x in (page_targets() or []) if x.get("id") == win[1]), None)
        if not t:
            return "", "", "", True, True
        return t.get("url", ""), t.get("title", ""), "", True, False


def js_error_help(err):
    return ("Guardian Monitor couldn't read the page in its Chrome window. If this keeps showing, close that "
            "Chrome window, then press Stop and Start. Chrome said: " + err.strip()[:200])


def bring_to_front(win):
    if not win:
        return
    set_window_state(win, "normal")
    try:
        browser_call("Target.activateTarget", {"targetId": win[1]})
    except Exception:
        pass
    try:
        page_call(win, "Page.bringToFront", timeout=10)
    except Exception:
        pass
    t = next((x for x in (page_targets() or []) if x.get("id") == win[1]), None)
    _force_foreground((t or {}).get("title", ""))


# ---------------- Target/Walmart checker windows (only used if "invisible" is turned off) ----------------
def checker_tab(name, url):
    c = W.checkers[name]
    if c["tab"] and window_exists(c["tab"]):
        go_to(c["tab"], url)
        set_window_state(c["tab"], "minimized")
        return c["tab"]
    win, err = new_window(url, True)
    if not win:
        raise RuntimeError("couldn't open a Chrome window" + (f" ({err[:80]})" if err else ""))
    c["tab"] = win
    return win


def background_tab(url):
    """A background tab in Guardian Monitor's Chrome (nothing pops up). Its own minimized window if Chrome is closed."""
    if page_targets() is None:
        return None, True
    try:
        tid = browser_call("Target.createTarget", {"url": url, "background": True})["targetId"]
        return ("cdp", tid), False
    except Exception:
        return None, True


def close_checker_tab(name):
    c = W.checkers[name]
    if c["tab"]:
        try:
            browser_call("Target.closeTarget", {"targetId": c["tab"][1]}, timeout=5)
        except Exception:
            pass
        c["tab"] = None


def open_link(url):
    if not str(url).startswith(("http://", "https://")):
        return False
    try:
        if IS_WIN:
            os.startfile(url)
        else:
            import webbrowser
            webbrowser.open(url)
        return True
    except Exception:
        return False


def open_in_chrome(url):
    """A restock or open drawing: show it in your normal browser, where you're signed in."""
    open_link(url)


# ---------------- hidden page reading (Target/Walmart checkers) ----------------
def chrome_user_agent():
    if "ua" not in W._chrome_ua:
        W._chrome_ua["ua"] = (f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                              f"(KHTML, like Gecko) Chrome/{chrome_version_major()}.0.0.0 Safari/537.36")
    return W._chrome_ua["ua"]


def _kill_tree(proc):
    if IS_WIN:
        try:
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True,
                           timeout=20, creationflags=NO_WINDOW)
        except Exception:
            pass
    else:
        import signal
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except Exception:
            pass
    try:
        proc.wait(timeout=5)
    except Exception:
        pass


def dump_dom(name, url):
    """The finished page from a hidden copy of Chrome: no window, no taskbar button.
    Every Chrome process it starts is closed afterwards."""
    profile = os.path.join(W.DATA_DIR, f"checker-chrome-{name}")
    os.makedirs(profile, exist_ok=True)
    cmd = [W.CHROME_BIN, "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
           "--hide-scrollbars", "--mute-audio", "--disable-extensions", f"--user-data-dir={profile}",
           "--window-size=1280,2400", f"--user-agent={W.chrome_user_agent()}", "--lang=en-US",
           "--virtual-time-budget=15000", "--dump-dom", url]
    if os.environ.get("QW_HEADLESS_SANDBOX_OFF"):
        cmd.insert(1, "--no-sandbox")
    proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            creationflags=NO_WINDOW | NEW_GROUP,
                            **({} if IS_WIN else {"start_new_session": True}))
    try:
        raw, _ = proc.communicate(timeout=90)
    except subprocess.TimeoutExpired:
        raw = None
    finally:
        _kill_tree(proc)
    if raw is None:
        raise RuntimeError("the page took too long to load. It'll try again at the next check")
    return raw.decode("utf-8", "replace")


# ---------------- passwords: Windows Credential Manager ----------------
def _cred_target(url, user):
    return f"Queue Watcher: {W.site_host(url)}: {user}"   # kept so a saved login keeps working


def _advapi():
    import ctypes
    from ctypes import wintypes

    class FILETIME(ctypes.Structure):
        _fields_ = [("lo", wintypes.DWORD), ("hi", wintypes.DWORD)]

    class CREDENTIAL(ctypes.Structure):
        _fields_ = [("Flags", wintypes.DWORD), ("Type", wintypes.DWORD), ("TargetName", wintypes.LPWSTR),
                    ("Comment", wintypes.LPWSTR), ("LastWritten", FILETIME),
                    ("CredentialBlobSize", wintypes.DWORD), ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
                    ("Persist", wintypes.DWORD), ("AttributeCount", wintypes.DWORD),
                    ("Attributes", ctypes.c_void_p), ("TargetAlias", wintypes.LPWSTR),
                    ("UserName", wintypes.LPWSTR)]

    adv = ctypes.WinDLL("advapi32", use_last_error=True)
    adv.CredWriteW.argtypes = [ctypes.POINTER(CREDENTIAL), wintypes.DWORD]
    adv.CredWriteW.restype = wintypes.BOOL
    adv.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                              ctypes.POINTER(ctypes.POINTER(CREDENTIAL))]
    adv.CredReadW.restype = wintypes.BOOL
    adv.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
    adv.CredDeleteW.restype = wintypes.BOOL
    adv.CredFree.argtypes = [ctypes.c_void_p]
    adv.CredFree.restype = None
    return ctypes, adv, CREDENTIAL


CRED_TYPE_GENERIC = 1
CRED_PERSIST_LOCAL_MACHINE = 2


def kc_save(url, user, password):
    if not user or not password:
        return False, "Enter both your email/username and password."
    if any(c in user for c in '"\n\r\\'):
        return False, "That username has characters Credential Manager can't store this way."
    if not W.site_host(url):
        return False, "Set the website first."
    blob = password.encode("utf-8")
    if len(blob) > 2560:
        return False, "That password is too long to store."
    try:
        ctypes, adv, CREDENTIAL = _advapi()
        buf = (ctypes.c_ubyte * len(blob)).from_buffer_copy(blob)
        cred = CREDENTIAL()
        cred.Type = CRED_TYPE_GENERIC
        cred.TargetName = _cred_target(url, user)
        cred.UserName = user
        cred.CredentialBlobSize = len(blob)
        cred.CredentialBlob = ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte))
        cred.Persist = CRED_PERSIST_LOCAL_MACHINE
        ok = adv.CredWriteW(ctypes.byref(cred), 0)
    except Exception as e:
        return False, f"Couldn't reach Windows Credential Manager: {e}"
    if not ok:
        return False, "Windows Credential Manager said no. Try again."
    return True, f"Saved in Windows Credential Manager for {W.site_host(url)}."


def kc_load(url, user):
    if not user:
        return None
    try:
        ctypes, adv, CREDENTIAL = _advapi()
        p = ctypes.POINTER(CREDENTIAL)()
        if not adv.CredReadW(_cred_target(url, user), CRED_TYPE_GENERIC, 0, ctypes.byref(p)):
            return None
        try:
            c = p.contents
            return ctypes.string_at(c.CredentialBlob, c.CredentialBlobSize).decode("utf-8")
        finally:
            adv.CredFree(p)
    except Exception:
        return None


def kc_delete(url, user):
    try:
        ctypes, adv, _ = _advapi()
        adv.CredDeleteW(_cred_target(url, user), CRED_TYPE_GENERIC, 0)
    except Exception:
        pass


# ---------------- PowerShell helper (notifications, file windows, picture cropping) ----------------
def powershell(script, env=None, timeout=60):
    """Run a PowerShell script with no console window. Values go in through environment
    variables, so nothing ever needs quoting."""
    enc = base64.b64encode(script.encode("utf-16-le")).decode()
    try:
        r = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-STA", "-ExecutionPolicy",
                            "Bypass", "-EncodedCommand", enc], capture_output=True, timeout=timeout,
                           env={**os.environ, **(env or {})}, creationflags=NO_WINDOW)
        return r.returncode == 0, r.stdout.decode("utf-8", "replace").strip()
    except Exception as e:
        return False, str(e)


TOAST_PS = r"""
$ErrorActionPreference = 'Stop'
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
$x = New-Object Windows.Data.Xml.Dom.XmlDocument
$x.LoadXml($env:QW_TOAST)
$t = New-Object Windows.UI.Notifications.ToastNotification $x
$app = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe'
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($app).Show($t)
"""


def notify_mac(msg):
    """A Windows notification (the function keeps its Mac name so the shared code can call it)."""
    from xml.sax.saxutils import escape
    xml = ('<toast><visual><binding template="ToastGeneric"><text>Guardian Monitor</text>'
           f"<text>{escape(str(msg))}</text></binding></visual>"
           '<audio src="ms-winsoundevent:Notification.Default"/></toast>')
    if not IS_WIN:
        return
    threading.Thread(target=powershell, args=(TOAST_PS, {"QW_TOAST": xml}, 30), daemon=True).start()


CHOOSE_PS = r"""
Add-Type -AssemblyName System.Windows.Forms
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$owner = New-Object System.Windows.Forms.Form
$owner.TopMost = $true
$d = New-Object System.Windows.Forms.OpenFileDialog
$d.Title = $env:QW_TITLE
$d.Filter = $env:QW_FILTER
if ($d.ShowDialog($owner) -eq [System.Windows.Forms.DialogResult]::OK) { Write-Output $d.FileName }
"""


def choose_file(prompt, kinds):
    if any("movie" in k for k in kinds):
        flt = "Pictures and videos|*.jpg;*.jpeg;*.png;*.gif;*.webp;*.mp4;*.m4v;*.mov;*.webm"
    else:
        flt = "Pictures|*.jpg;*.jpeg;*.png;*.gif;*.webp;*.bmp"
    ok, out = powershell(CHOOSE_PS, {"QW_TITLE": prompt, "QW_FILTER": flt}, timeout=600)
    out = out.strip().splitlines()[-1] if out.strip() else ""
    return out if ok and out and os.path.exists(out) else None


def sips(*args):
    return False, ""          # Mac-only picture tool; HEIC photos aren't offered on Windows


CROP_PS = r"""
Add-Type -AssemblyName System.Drawing
$src = [System.Drawing.Image]::FromFile($env:QW_IN)
$s = [Math]::Min($src.Width, $src.Height)
$x = [int](($src.Width - $s) / 2); $y = [int](($src.Height - $s) / 2)
$b = New-Object System.Drawing.Bitmap 256, 256
$g = [System.Drawing.Graphics]::FromImage($b)
$g.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
$g.DrawImage($src, (New-Object System.Drawing.Rectangle 0, 0, 256, 256), (New-Object System.Drawing.Rectangle $x, $y, $s, $s), [System.Drawing.GraphicsUnit]::Pixel)
$b.Save($env:QW_OUT, [System.Drawing.Imaging.ImageFormat]::Png)
$g.Dispose(); $b.Dispose(); $src.Dispose()
"""


def square_png(path, out):
    """Center-crop a picture to a 256px square PNG."""
    try:
        from PIL import Image
        im = Image.open(path).convert("RGBA")
        s = min(im.size)
        x, y = (im.width - s) // 2, (im.height - s) // 2
        im.crop((x, y, x + s, y + s)).resize((256, 256), Image.LANCZOS).save(out, "PNG")
        return True
    except ImportError:
        pass
    except Exception:
        return False
    ok, _ = powershell(CROP_PS, {"QW_IN": path, "QW_OUT": out})
    return ok and os.path.exists(out)


def pick_picture():
    path = W.choose_file("Choose a picture for your Discord bot", ["public.image"])
    if not path:
        return {"ok": False, "msg": "No picture chosen."}
    os.makedirs(W.DATA_DIR, exist_ok=True)
    tmp = os.path.join(W.DATA_DIR, "bot-picture.png")
    try:
        os.remove(tmp)
    except FileNotFoundError:
        pass
    if not square_png(path, tmp):
        return {"ok": False, "msg": "Couldn't open that picture."}
    with open(tmp, "rb") as f:
        data = "data:image/png;base64," + base64.b64encode(f.read()).decode()
    return {"ok": True, "msg": "Picture ready. Click Update to send it to Discord.", "data": data}


# ---------------- keep the PC awake ----------------
_awake = {"why": set(), "thread": None}
_awake_lock = threading.Lock()


def _awake_loop():
    ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
    k32 = None
    if IS_WIN:
        import ctypes
        k32 = ctypes.WinDLL("kernel32")
    while True:
        with _awake_lock:
            on = bool(_awake["why"])
        if k32:
            # The screen can still turn off; only sleep is held off.
            k32.SetThreadExecutionState(ES_CONTINUOUS | (ES_SYSTEM_REQUIRED if on else 0))
        time.sleep(20)


def awake(reason, on):
    with _awake_lock:
        (_awake["why"].add if on else _awake["why"].discard)(reason)
        if not _awake["thread"]:
            _awake["thread"] = threading.Thread(target=_awake_loop, daemon=True)
            _awake["thread"].start()


def keep_awake(on):
    awake("schedule", on)
    W.sched["awake"] = True if on else None


_orig_watch = None


def watch(s, stop):
    awake("watch", True)
    try:
        return _orig_watch(s, stop)
    finally:
        awake("watch", False)


# ---------------- the app window ----------------
def panel_page():
    t = page_targets(PANEL_PORT)
    if not t:
        return None
    return next((x for x in t if x.get("url", "").startswith(f"http://127.0.0.1:{W.PORT}")), None)


def open_panel(url):
    """Show the control panel in its own window. Returns True if it opened."""
    page = panel_page()
    if page:
        try:
            _get_json(PANEL_PORT, f"/json/activate/{page['id']}")
        except Exception:
            pass
        return True
    exe = find_panel_browser()
    if not exe:
        return False
    prof = os.path.join(W.DATA_DIR, "window-profile")
    os.makedirs(prof, exist_ok=True)
    cmd = [exe, f"--app={url}?app=1", f"--user-data-dir={prof}", f"--remote-debugging-port={PANEL_PORT}",
           "--no-first-run", "--no-default-browser-check", "--window-size=1180,900", "--disable-sync",
           "--hide-crash-restore-bubble", "--disable-features=Translate"]
    if os.environ.get("QW_HEADLESS_SANDBOX_OFF"):
        cmd.append("--no-sandbox")
    try:
        subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         creationflags=NEW_GROUP, close_fds=True,
                         **({} if IS_WIN else {"start_new_session": True}))
        return True
    except Exception:
        return False


def panel_running():
    """True while the app window is open (the window's page is there)."""
    return panel_page() is not None


def close_panel():
    try:
        browser_call("Browser.close", port=PANEL_PORT, timeout=5)
    except Exception:
        pass


def shutdown(quit_window=True):
    W.stop_event.set()
    W.ack_event.set()
    W.checker_stop.set()
    for n in W.checkers:
        try:
            close_checker_tab(n)
        except Exception:
            pass
    try:
        keep_awake(False)
    except Exception:
        pass
    close_panel()          # also closes a leftover hidden window process
    os._exit(0)


def message_box(title, text):
    if IS_WIN:
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, text, title, 0x30)
            return
        except Exception:
            pass
    print(f"{title}: {text}")


STATS_PS = r"""
$ErrorActionPreference = 'SilentlyContinue'
$cpu = (Get-CimInstance Win32_Processor | Measure-Object -Property LoadPercentage -Average).Average
$os = Get-CimInstance Win32_OperatingSystem
$free = if ($os.TotalVisibleMemorySize) { [math]::Round(100 * $os.FreePhysicalMemory / $os.TotalVisibleMemorySize) } else { $null }
$t = $null
$z = Get-CimInstance Win32_PerfFormattedData_Counters_ThermalZoneInformation
if ($z) { $k = ($z | Measure-Object -Property HighPrecisionTemperature -Maximum).Maximum; if ($k -gt 0) { $t = [math]::Round($k / 10 - 273.15, 1) } }
$killed = 0
Get-CimInstance Win32_Process -Filter "Name='chrome.exe'" | Where-Object {
  $_.CommandLine -like '*checker-chrome-*' -and $_.CommandLine -like '*--headless*' -and $_.CreationDate -lt (Get-Date).AddMinutes(-3)
} | ForEach-Object { Stop-Process -Id $_.ProcessId -Force; $killed++ }
@{ cpu = $cpu; mem_free = $free; temp_c = $t; killed = $killed } | ConvertTo-Json -Compress
"""
_last_killed = {"n": 0}


def read_stats():
    """CPU, memory and (if Windows reports it) temperature, in one quick hidden PowerShell call."""
    out = {"thermal": None, "cpu": None, "mem_free": None, "temp_c": None}
    if not IS_WIN:
        return out
    ok, txt = powershell(STATS_PS, timeout=40)
    try:
        d = json.loads(txt.strip().splitlines()[-1])
        out["cpu"] = round(d["cpu"]) if d.get("cpu") is not None else None
        out["mem_free"] = d.get("mem_free")
        t = d.get("temp_c")
        out["temp_c"] = t if t and 10 < t < 130 else None      # ignore nonsense readings
        _last_killed["n"] = int(d.get("killed") or 0)
    except Exception:
        pass
    return out


def clean_stray_chrome():
    n, _last_killed["n"] = _last_killed["n"], 0
    return n


def restart_app():
    """After an update: run the launcher, which closes this copy and opens the new one."""
    time.sleep(1)
    here = os.path.dirname(os.path.abspath(W.__file__))
    pyw = sys.executable
    if IS_WIN and pyw.lower().endswith("python.exe"):
        cand = pyw[:-10] + "pythonw.exe"
        pyw = cand if os.path.exists(cand) else pyw
    subprocess.Popen([pyw, os.path.join(here, "launcher.pyw")], cwd=here, stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True,
                     creationflags=(0x00000008 | NEW_GROUP | NO_WINDOW) if IS_WIN else 0,
                     **({} if IS_WIN else {"start_new_session": True}))


def osa(script, timeout=20):
    return "", "AppleScript isn't available on Windows"


def main():
    W.load_settings()
    try:
        server = W.ThreadingHTTPServer(("127.0.0.1", W.PORT), W.Handler)
    except OSError:
        message_box("Guardian Monitor can't start",
                    f"Another program is using port {W.PORT}. Guardian Monitor may already be open. "
                    "If not, restart your PC and try again.")
        return
    url = f"http://127.0.0.1:{W.PORT}/"
    W.log(f"Guardian Monitor {W.VERSION} (Windows) is running.")
    threading.Thread(target=W.scheduler_loop, daemon=True).start()
    threading.Thread(target=W.news_loop, daemon=True).start()
    threading.Thread(target=W.update_loop, daemon=True).start()
    threading.Thread(target=W.phone_control_loop, daemon=True).start()
    threading.Thread(target=W.health_loop, daemon=True).start()
    threading.Thread(target=W.apply_branding, daemon=True).start()
    W.apply_roles()
    for n in W.checkers:
        threading.Thread(target=W.checker_loop, args=(n,), daemon=True).start()
    W.write_window_key()
    if os.environ.get("QW_NO_WINDOW"):
        pass                                   # tests
    elif open_panel(url):
        W.PANEL = "window"
        threading.Thread(target=W.panel_monitor, args=(url,), daemon=True).start()
    else:
        open_link(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


# ---------------- hook everything in ----------------
REPLACE = ["chrome_running", "tab_do", "tab_get", "find_tab", "open_window", "window_exists", "reload_tab", "close_tab",
           "go_to", "run_js", "wait_for_load", "read_page", "js_error_help", "bring_to_front", "checker_tab", "background_tab",
           "close_checker_tab", "open_link", "open_in_chrome", "chrome_user_agent", "dump_dom", "restart_app",
           "kc_save", "kc_load", "kc_delete", "notify_mac", "choose_file", "sips", "pick_picture",
           "keep_awake", "watch", "panel_running", "shutdown", "osa", "main", "read_stats", "clean_stray_chrome"]


def install(g):
    """Swap the Mac-only pieces of the main app for the Windows ones above."""
    global W, _orig_watch
    W = sys.modules[g["__name__"]]
    _orig_watch = g["watch"]
    for name in REPLACE:
        g[name] = globals()[name]
    g["CHROME_BIN"] = find_chrome()
    g["HEALTH_EVERY"] = 120          # the Windows check uses PowerShell, so run it a bit less often
    g["PLATFORM"] = "windows"
