#!/usr/bin/env python3
"""
Guardian Monitor - Pokemon Center queue alert app (Windows + Google Chrome)

Runs a small control panel in its own window (only on this PC).
Start watching -> refreshes Pokemon Center in its own Chrome window.
Queue found   -> stops refreshing, pings phone (ntfy) + Discord + Mac, then turns off.
"""

import json
import os
import random
import secrets
import ssl
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

PORT = 8765
VERSION = "4.7"   # the launcher compares this, and replaces an older copy that's still running
PLATFORM = "mac"  # the Windows version switches this to "windows"
TOKEN = secrets.token_hex(16)
PAGE_LOAD_WAIT = 8

DATA_DIR = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "QueueWatcher")
SETTINGS_FILE = os.path.join(DATA_DIR, "settings.json")
WALLPAPER_FILE = os.path.join(DATA_DIR, "wallpaper.bin")
WALLPAPER_META = os.path.join(DATA_DIR, "wallpaper.json")
MAX_WALLPAPER = 300 * 1024 * 1024

DEFAULTS = {
    "watch_url": "https://www.pokemoncenter.com/",
    "min_wait": 30,
    "max_wait": 60,
    "ntfy_topic": "",
    "discord_webhook": "",           # PKC queue channel (and anything without its own channel)
    "discord_webhook_target": "",    # blank = use the main webhook
    "discord_webhook_walmart": "",   # blank = use the main webhook
    "discord_role_target": "",       # role ID to ping for Target restocks (blank = same as queue alerts)
    "discord_role_walmart": "",      # role ID to ping for open Walmart drawings (blank = same as queue alerts)
    "discord_webhook_bestbuy": "",   # blank = use the main webhook
    "discord_role_bestbuy": "",      # role ID to ping for Best Buy restocks (blank = same as queue alerts)
    "discord_role_all": "",          # extra role @'d on EVERY real alert (your server's Pokémon role)
    "roles_v1": False,
    "roles_v2": False,
    "roles_v3": False,
    "roles_v4": False,
    "brand_v2": False,
    "update_pinged": "",             # newest version your phone was already told about
    "discord_user_id": "",          # user ID or role ID, used with "user"/"role" below
    "discord_mention": "user",       # none | here | everyone | role | user
    "queue_words": "queue, in line, waiting room, keep your browser open, resist the urge to refresh",
    "block_words": "pardon our interruption, something's gone wrong, incapsula incident, "
                   "request unsuccessful, access denied, are you a robot, error 15, error 17",
    # Site down/overloaded often happens right as a drop starts: ping once, keep watching.
    "headsup_words": "temporarily unavailable",
    # Pokemon Center's queue runs on an Imperva waiting room, whose page loads a
    # "_Incapsula_Resource?SWWRGTS" script. Spotting that script = queue.
    "use_script_check": True,
    # When to watch: "smart" (drop hours), "custom" (your own week), "manual" (only when you press Start)
    "schedule_mode": "smart",
    "schedule_custom": {str(d): {"on": d < 5, "start": "09:45", "end": "14:30"} for d in range(7)},
    # Staying signed in
    "pkc_background": True,
    "brand_v1": False,
    "health_mode": "protect",        # protect (ease off / pause when hot) | warn (just tell me) | off               # set once the Discord bot got the Guardian Monitor name + picture
    "phone_control": True,           # Start / Stop / Status from the ntfy app          # Pokémon Center window opens minimized, pops up on a queue
    "check_signed_in": True,
    "auto_login": False,
    "login_url": "",
    "login_user": "",
    "login_saved": False,
    "signed_in_words": "sign out, log out, my account, order history",
    "signed_out_words": "sign in, log in, create account",
    # Target restocks & Walmart drawings (each runs on its own)
    "target_enabled": False,
    "target_watch": "",
    "target_scan": True,
    "target_hours": "always",
    "target_every": 5,
    "target_open_page": True,
    "target_mention": "same",          # same | none | here | everyone
    "discord_target_msg": "\U0001F3AF **Target restock:** {item} for {price}\n{link}",
    "checker_invisible": True,
    "walmart_enabled": False,
    "walmart_every": 20,
    "walmart_msrp_only": True,
    "walmart_products": True,        # also check Walmart's Pokémon card products for MSRP restocks
    "walmart_scan": True,
    "walmart_watch": "",
    "walmart_open_page": True,
    "walmart_mention": "same",
    "discord_walmart_msg": "\U0001F6D2 **Walmart restock:** {item} for {price}\n{link}",
    # Best Buy restocks (works like Target)
    "bestbuy_enabled": False,
    "bestbuy_watch": "",
    "bestbuy_scan": True,
    "bestbuy_every": 5,
    "bestbuy_open_page": True,
    "bestbuy_mention": "same",         # same | none | here | everyone
    "discord_bestbuy_msg": "\U0001F7E6 **Best Buy restock:** {item} for {price}\n{link}",
    "bestbuy_v1": False,
    "bestbuy_test_done": False,      # the one-time "Best Buy checker works" test post
    "msrp_limits": "",               # Target's own prices
    "msrp_bestbuy": "",
    "msrp_walmart": "",
    "msrp_allowance": 10,
    # Releases & drops news: one source per line, "Name | feed link"
    "news_feeds": "Bill's Archive (release calendar) | https://billsarchive.com/calendar.html\n"
                  "Pok\u00e9mon (official news) | https://www.pokemon.com/us/pokemon-news\n"
                  "PokeBeach | https://www.pokebeach.com/feed\n"
                  "Pok\u00e9Guardian | https://www.pokeguardian.com/feed\n"
                  "Pok\u00e9Jungle | https://pokejungle.net/feed/",
    # Discord look and wording
    "discord_enabled": True,
    "discord_name": "",              # blank = use the webhook's own name
    "discord_queue_msg": "\U0001F6A8 **Pok\u00e9mon Center queue is live!** Get in line now: {link}",
    "discord_headsup_msg": "\u26A0\uFE0F **Heads up:** Pok\u00e9mon Center looks overloaded. "
                           "A drop may be starting soon. {link}",
}
OLD_DEFAULTS = {
    "news_feeds": ["PokeBeach | https://www.pokebeach.com/feed\n"
                   "Pok\u00e9Jungle | https://pokejungle.net/feed/",
                   "PokeBeach | https://www.pokebeach.com/feed\n"
                   "Pok\u00e9Jungle | https://pokejungle.net/feed/\n"
                   "r/PokemonRestocks (community reports) | https://www.reddit.com/r/PokemonRestocks/new/.rss"],
    "queue_words": ["queue, in line, waiting room"],
    "block_words": ["pardon our interruption, something's gone wrong, incapsula incident, "
                    "request unsuccessful, access denied, are you a robot"],
}
SCRIPT_MARK = "[[qw-waiting-room-script]]"
HEADSUP_COOLDOWN = 15 * 60

REPEAT_ALERTS = 10        # queue alerts repeat until you press "I'm on it" (max this many)
REPEAT_EVERY = 60         # seconds between repeat alerts
PHONE_ACK_CHECK = 5       # how often to check if you tapped "I'm on it" on your phone
MAX_READ_FAILS = 3        # this many failed page reads in a row = something's wrong

lock = threading.Lock()
state = {
    "status": "idle",        # idle | watching | found | error
    "message": "Ready. Press Start to begin watching.",
    "checks": 0,
    "last_check": "",
    "js_warning": False,
    "js_help": "",
    "log": [],
}
stop_event = threading.Event()
ack_event = threading.Event()
watcher_thread = None


# ---------------- settings ----------------
settings_lock = threading.RLock()


def load_settings():
    s = dict(DEFAULTS)
    with settings_lock:
        for attempt in range(3):
            try:
                with open(SETTINGS_FILE) as f:
                    s.update(json.load(f))
                break
            except FileNotFoundError:
                break
            except Exception:                 # damaged file (saves are atomic, so it isn't a half-write):
                time.sleep(0.05)              # start fresh from the defaults below
    if not str(s.get("msrp_limits", "")).strip() or s.get("msrp_limits") in OLD_MSRP_DEFAULTS:
        s["msrp_limits"] = MSRP_DEFAULT
    for key, olds in OLD_DEFAULTS.items():   # upgrade untouched old defaults
        if s.get(key) in olds:
            s[key] = DEFAULTS[key]
    if not s.get("ntfy_topic"):
        with settings_lock:
            try:                              # someone else may have just saved one
                with open(SETTINGS_FILE) as f:
                    cur = json.load(f).get("ntfy_topic")
            except Exception:
                cur = None
            s["ntfy_topic"] = cur or "pkc-queue-" + secrets.token_hex(4)
            if not cur:
                save_settings(s)
    return s


def save_settings(s):
    """Save atomically (write a temp file, then swap it in), so a reader never sees a half-written file."""
    os.makedirs(DATA_DIR, exist_ok=True)
    with settings_lock:
        tmp = SETTINGS_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(s, f, indent=2)
        os.replace(tmp, SETTINGS_FILE)


def clean_time(t, fallback):
    try:
        h, m = str(t).strip().split(":")[:2]
        h, m = int(h), int(m)
        if 0 <= h <= 23 and 0 <= m <= 59:
            return f"{h:02d}:{m:02d}"
    except Exception:
        pass
    return fallback


def clean_week(week):
    out = {}
    week = week if isinstance(week, dict) else {}
    for d in range(7):
        day = week.get(str(d)) if isinstance(week.get(str(d)), dict) else {}
        out[str(d)] = {
            "on": day.get("on", d < 5) in (True, "true", "on", 1, "1"),
            "start": clean_time(day.get("start"), "09:45"),
            "end": clean_time(day.get("end"), "14:30"),
        }
    return out


def clean_settings(new):
    s = load_settings()
    for key in DEFAULTS:
        if key in new:
            s[key] = new[key]
    try:
        s["min_wait"] = max(15, int(s["min_wait"]))
    except Exception:
        s["min_wait"] = DEFAULTS["min_wait"]
    try:
        s["max_wait"] = max(s["min_wait"], int(s["max_wait"]))
    except Exception:
        s["max_wait"] = max(s["min_wait"], DEFAULTS["max_wait"])
    for key in ("watch_url", "ntfy_topic", "discord_webhook", "discord_user_id",
                "queue_words", "block_words", "headsup_words", "news_feeds",
                "login_url", "login_user", "signed_in_words", "signed_out_words"):
        s[key] = str(s[key]).strip()
    s["use_script_check"] = s["use_script_check"] in (True, "true", "on", 1, "1")
    s["discord_enabled"] = s["discord_enabled"] in (True, "true", "on", 1, "1")
    for key in ("target_enabled", "target_scan", "target_open_page", "walmart_enabled", "walmart_msrp_only",
                "checker_invisible", "bestbuy_enabled", "bestbuy_scan", "bestbuy_open_page",
                "walmart_products", "walmart_scan", "walmart_open_page"):
        s[key] = s[key] in (True, "true", "on", 1, "1")
    try:
        s["msrp_allowance"] = round(min(100.0, max(0.0, float(str(s["msrp_allowance"]).lstrip("$")))), 2)
    except Exception:
        s["msrp_allowance"] = DEFAULTS["msrp_allowance"]
    for key, lo, hi in (("target_every", 2, 240), ("walmart_every", 10, 720), ("bestbuy_every", 3, 240)):
        try:
            s[key] = min(hi, max(lo, int(s[key])))
        except Exception:
            s[key] = DEFAULTS[key]
    if s.get("target_mention") not in ("same", "none", "here", "everyone"):
        s["target_mention"] = "same"
    s["discord_target_msg"] = str(s.get("discord_target_msg", "")).strip()[:1800] or DEFAULTS["discord_target_msg"]
    if s["target_hours"] not in ("always", "overnight"):
        s["target_hours"] = "always"
    links = []
    for u in str(s["target_watch"]).split():
        u = u.strip()
        if u.startswith("target.com") or u.startswith("www.target.com"):
            u = "https://" + u
        if _re.match(r"https?://(www\.)?target\.com/", u) and u not in links:
            links.append(u)
    s["target_watch"] = "\n".join(links[:10])
    if s.get("bestbuy_mention") not in ("same", "none", "here", "everyone"):
        s["bestbuy_mention"] = "same"
    s["discord_bestbuy_msg"] = str(s.get("discord_bestbuy_msg", "")).strip()[:1800] or DEFAULTS["discord_bestbuy_msg"]
    links = []
    for u in str(s.get("bestbuy_watch", "")).split():
        u = u.strip()
        if u.startswith(("bestbuy.com", "www.bestbuy.com")):
            u = "https://" + u
        if _re.match(r"https?://(www\.)?bestbuy\.com/", u) and u not in links:
            links.append(u)
    s["bestbuy_watch"] = "\n".join(links[:10])
    if s.get("walmart_mention") not in ("same", "none", "here", "everyone"):
        s["walmart_mention"] = "same"
    s["discord_walmart_msg"] = str(s.get("discord_walmart_msg", "")).strip()[:1800] or DEFAULTS["discord_walmart_msg"]
    links = []
    for u in str(s.get("walmart_watch", "")).split():
        u = u.strip()
        if u.startswith(("walmart.com", "www.walmart.com")):
            u = "https://" + u
        if _re.match(r"https?://(www\.)?walmart\.com/ip/", u) and u not in links:
            links.append(u)
    s["walmart_watch"] = "\n".join(links[:10])
    s["bestbuy_v1"] = s.get("bestbuy_v1") in (True, "true", 1, "1")
    s["bestbuy_test_done"] = s.get("bestbuy_test_done") in (True, "true", 1, "1")
    s["msrp_limits"] = str(s["msrp_limits"]).strip()[:3000] or MSRP_DEFAULT
    for st in MSRP_STORES:
        s[f"msrp_{st}"] = str(s.get(f"msrp_{st}", "")).strip()[:3000] or MSRP_GENERAL
    s["pkc_background"] = s.get("pkc_background", True) in (True, "true", "on", 1, "1")
    s["brand_v1"] = s.get("brand_v1") in (True, "true", 1, "1")
    if s.get("health_mode") not in ("protect", "warn", "off"):
        s["health_mode"] = "protect"
    s["phone_control"] = s.get("phone_control", True) in (True, "true", "on", 1, "1")
    for key in ("check_signed_in", "auto_login", "login_saved"):
        s[key] = s[key] in (True, "true", "on", 1, "1")
    if s["login_url"] and not s["login_url"].startswith(("http://", "https://")):
        s["login_url"] = ""
    s["login_user"] = s["login_user"][:200]
    if s.get("schedule_mode") not in ("smart", "custom", "manual"):
        s["schedule_mode"] = "smart"
    s["schedule_custom"] = clean_week(s.get("schedule_custom"))
    s["discord_name"] = str(s["discord_name"]).strip()[:80]
    for key in ("discord_queue_msg", "discord_headsup_msg"):
        s[key] = str(s[key]).strip()[:1800] or DEFAULTS[key]
    if not split_words(s["queue_words"]) and not s["use_script_check"]:
        s["queue_words"] = DEFAULTS["queue_words"]
    if not s["watch_url"].startswith("http"):
        s["watch_url"] = DEFAULTS["watch_url"]
    s["ntfy_topic"] = "".join(c for c in s["ntfy_topic"] if c.isalnum() or c in "-_")
    s["discord_user_id"] = "".join(c for c in s["discord_user_id"] if c.isdigit())
    for key in ("discord_role_target", "discord_role_walmart", "discord_role_bestbuy", "discord_role_all"):
        s[key] = "".join(c for c in str(s.get(key, "")) if c.isdigit())[:25]
    s["roles_v1"] = s.get("roles_v1") in (True, "true", 1, "1")
    s["roles_v2"] = s.get("roles_v2") in (True, "true", 1, "1")
    s["roles_v3"] = s.get("roles_v3") in (True, "true", 1, "1")
    s["roles_v4"] = s.get("roles_v4") in (True, "true", 1, "1")
    s["brand_v2"] = s.get("brand_v2") in (True, "true", 1, "1")
    s["update_pinged"] = str(s.get("update_pinged", ""))[:20]
    for key in ("discord_webhook", "discord_webhook_target", "discord_webhook_walmart", "discord_webhook_bestbuy"):
        u = str(s.get(key, "")).strip()
        s[key] = u if u.startswith(("https://", "http://")) else ""
    if s.get("discord_mention") not in ("none", "here", "everyone", "role", "user"):
        s["discord_mention"] = "user"
    return s


def wallpaper_meta():
    try:
        with open(WALLPAPER_META) as f:
            meta = json.load(f)
        return meta if os.path.exists(WALLPAPER_FILE) else None
    except Exception:
        return None


_ssl_ctx = None


def ssl_context():
    """Python from python.org on a Mac has no certificates until you run its
    'Install Certificates' step, so HTTPS pings would fail. Fall back to the Mac's
    own trusted certificates in that case."""
    global _ssl_ctx
    if _ssl_ctx:
        return _ssl_ctx
    ctx = ssl.create_default_context()
    try:
        import certifi  # present if the user installed it
        ctx = ssl.create_default_context(cafile=certifi.where())
    except Exception:
        paths = ssl.get_default_verify_paths()
        has_certs = (paths.cafile and os.path.exists(paths.cafile)) or (
            paths.capath and os.path.isdir(paths.capath) and os.listdir(paths.capath))
        if not has_certs:
            pem = os.path.join(DATA_DIR, "mac-root-certs.pem")
            try:
                os.makedirs(DATA_DIR, exist_ok=True)
                out = subprocess.run(
                    ["security", "find-certificate", "-a", "-p",
                     "/System/Library/Keychains/SystemRootCertificates.keychain"],
                    capture_output=True, text=True, timeout=20).stdout
                if "BEGIN CERTIFICATE" in out:
                    with open(pem, "w") as f:
                        f.write(out)
                    ctx = ssl.create_default_context(cafile=pem)
            except Exception:
                pass
    _ssl_ctx = ctx
    return ctx


def fetch(req, timeout=10):
    """urlopen with the right certificates; plain http (local tests) needs none."""
    url = req if isinstance(req, str) else req.full_url
    if url.startswith("https://"):
        return urllib.request.urlopen(req, timeout=timeout, context=ssl_context())
    return urllib.request.urlopen(req, timeout=timeout)


# ---------------- helpers ----------------
def log(msg):
    line = f"[{datetime.now().strftime('%I:%M:%S %p')}] {msg}"
    print(line, flush=True)
    with lock:
        state["log"].append(line)
        state["log"] = state["log"][-200:]


def set_state(**kw):
    with lock:
        state.update(kw)


def osa(script, timeout=20):
    """Run AppleScript. Returns (output, error)."""
    try:
        r = subprocess.run(["osascript", "-"], input=script, capture_output=True,
                           text=True, timeout=timeout)
        return r.stdout.strip(), r.stderr.strip()
    except Exception as e:
        return "", str(e)


def as_str(text):
    """Make text safe to put inside an AppleScript string."""
    return str(text).replace("\\", "\\\\").replace('"', '\\"')


# ---------------- notifications ----------------
def ack_topic(s):
    """Hidden ntfy topic the phone's "I'm on it" button posts to. You don't subscribe to it."""
    return f"{s['ntfy_topic']}-ack"


def notify_phone(s, title, msg, ack_button=False):
    if not s["ntfy_topic"]:
        return
    headers = {"Title": title, "Priority": "urgent",
               "Tags": "rotating_light", "Click": s["watch_url"]}
    if ack_button:
        # Adds an "I'm on it" button to the notification. Tapping it posts to the
        # ack topic, which the app checks to stop the reminders.
        headers["Actions"] = json.dumps([{
            "action": "http", "label": "I'm on it", "url": f"https://ntfy.sh/{ack_topic(s)}",
            "method": "POST", "body": "ack", "clear": True}])
    try:
        req = urllib.request.Request(
            f"https://ntfy.sh/{s['ntfy_topic']}",
            data=msg.encode("utf-8"),
            headers=headers,
        )
        fetch(req, timeout=10)
        log("Phone ping sent.")
    except Exception as e:
        log(f"Phone ping failed: {e}")


def discord_mention(s):
    """Who gets @'d in the Discord server, plus Discord's permission for that @.
    Your "every alert" role (the Pokémon role) is added to any alert that @'s someone."""
    tag, allowed = _discord_mention(s)
    extra = s.get("discord_role_all", "")
    if tag and extra and f"<@&{extra}>" not in tag:
        tag = f"{tag}<@&{extra}> "
        allowed = dict(allowed)
        allowed["roles"] = list(allowed.get("roles", [])) + [extra]
    return tag, allowed


def _discord_mention(s):
    who, the_id = s.get("discord_mention", "user"), s.get("discord_user_id", "")
    if who == "here":
        return "@here ", {"parse": ["everyone"]}
    if who == "everyone":
        return "@everyone ", {"parse": ["everyone"]}
    if who == "role" and the_id:
        return f"<@&{the_id}> ", {"roles": [the_id]}
    if who == "user" and the_id:
        return f"<@{the_id}> ", {"users": [the_id]}
    return "", {"parse": []}


def bad_discord_name(name):
    """Discord refuses bot names containing these words."""
    return any(w in name.lower() for w in ("discord", "clyde"))


def discord_text(s, key):
    """Your custom message with {link} filled in."""
    return s[key].replace("{link}", f"<{s['watch_url']}>")


BOT_PIC = "https://raw.githubusercontent.com/kylerredfield24-sys/queue-watcher/main/assets/guardian.png"


def webhook_for(s, channel=None):
    """Target and Walmart can each post to their own channel; otherwise the main webhook."""
    if channel in ("target", "walmart", "bestbuy") and s.get(f"discord_webhook_{channel}"):
        return s[f"discord_webhook_{channel}"]
    return s["discord_webhook"]


def notify_discord(s, msg, mention=True, force=False, channel=None):
    """Server-friendly message: your wording, your bot name, and an optional @."""
    hook = webhook_for(s, channel)
    if not hook or not (s["discord_enabled"] or force):
        return
    tag, allowed = discord_mention(s) if mention else ("", {"parse": []})
    payload = {"content": tag + msg, "allowed_mentions": allowed}
    if s["discord_name"] and not bad_discord_name(s["discord_name"]):
        payload["username"] = s["discord_name"]
    if hook != s["discord_webhook"]:      # extra channels get the logo without a separate setup step
        payload["avatar_url"] = BOT_PIC
    try:
        req = urllib.request.Request(
            hook,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "User-Agent": "QueueWatcher/1.0"},
        )
        fetch(req, timeout=10)
        log("Discord ping sent.")
    except Exception as e:
        log(f"Discord ping failed: {e}")


def with_role(s, key):
    """Settings that @ the role saved under `key` (if any) instead of the queue-alert @."""
    role = s.get(key, "")
    return dict(s, discord_mention="role", discord_user_id=role) if role else s


def notify_mac(msg):
    osa(f'display notification "{as_str(msg)}" with title "Guardian Monitor" sound name "Glass"')


def set_discord_profile(s, name, avatar_data_url):
    """Change the webhook's saved name and/or picture. Returns (ok, message)."""
    if not s["discord_webhook"]:
        return False, "Add your Discord webhook URL first."
    body = {}
    if name:
        if bad_discord_name(name):
            return False, "Discord doesn't allow names with \"discord\" or \"clyde\" in them."
        body["name"] = name[:80]
    if avatar_data_url:
        body["avatar"] = avatar_data_url
    if not body:
        return False, "Type a name or choose a picture first."
    try:
        req = urllib.request.Request(
            s["discord_webhook"], data=json.dumps(body).encode("utf-8"), method="PATCH",
            headers={"Content-Type": "application/json", "User-Agent": "QueueWatcher/1.0"})
        fetch(req, timeout=15)
        log("Discord name/picture updated.")
        return True, "Updated! New messages will use this name and picture."
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:200]
        log(f"Discord profile update failed: {e.code} {detail}")
        return False, f"Discord said no ({e.code}). Check the webhook URL, or try a smaller picture."
    except Exception as e:
        return False, f"Couldn't reach Discord: {e}"


def phone_acked(s, since):
    """True if the phone's "I'm on it" button was tapped after `since` (unix time)."""
    if not s["ntfy_topic"]:
        return False
    try:
        url = f"https://ntfy.sh/{ack_topic(s)}/json?poll=1&since={int(since)}"
        with fetch(url, timeout=10) as r:
            for line in r.read().decode("utf-8", "replace").splitlines():
                if line.strip() and json.loads(line).get("event") == "message":
                    return True
    except Exception:
        pass
    return False


import re as _re
_TZ_WORDS = {"PDT": -7, "PST": -8, "PT": None, "PACIFIC": None, "MDT": -6, "MST": -7, "MT": None, "MOUNTAIN": None,
             "CDT": -5, "CST": -6, "CT": None, "CENTRAL": None, "AKDT": -8, "AKST": -9, "HST": -10,
             "UTC": 0, "GMT": 0, "ET": "et", "EDT": "et", "EST": "et", "EASTERN": "et"}
_DST_BASE = {"PT": -8, "PACIFIC": -8, "MT": -7, "MOUNTAIN": -7, "CT": -6, "CENTRAL": -6}
_T = r"(\d{1,2})(?::(\d{2}))?\s*(a|p)\.?\s*m\.?"
_ZONE = r"\(?\b(PDT|PST|PT|MDT|MST|MT|CDT|CST|CT|AKDT|AKST|HST|UTC|GMT|ET|EDT|EST|Pacific|Mountain|Central|Eastern)\b(?:\s+time)?\)?"
TIME_ZONE_RX = _re.compile(_T + r"(?:\s*(?:-|–|—|to|until)\s*" + _T + r")?\s*" + _ZONE, _re.I)


def _us_dst(d=None):
    d = d or datetime.utcnow()
    from datetime import date as _date
    y = d.year
    start = _date(y, 3, 8 + (6 - _date(y, 3, 8).weekday()) % 7)      # 2nd Sunday of March
    end = _date(y, 11, 1 + (6 - _date(y, 11, 1).weekday()) % 7)      # 1st Sunday of November
    return start <= d.date() < end


def to_eastern(text):
    """Rewrite any '2:00pm PDT' / '4 PM CT' / '2pm - 3pm Pacific' in an alert as Eastern time."""
    if not text:
        return text
    dst = _us_dst()
    et_off = -4 if dst else -5

    def conv(h, mi, ap, off):
        mins = (int(h) % 12 + (12 if ap.lower() == "p" else 0)) * 60 + int(mi or 0) + (et_off - off) * 60
        nxt = mins >= 24 * 60
        mins %= 24 * 60
        hh, mm = divmod(mins, 60)
        return f"{(hh % 12) or 12}:{mm:02d} {'PM' if hh >= 12 else 'AM'}", nxt

    def fix(m):
        zone = m.group(7).upper()
        off = _TZ_WORDS.get(zone)
        if off is None:
            off = _DST_BASE[zone] + (1 if dst else 0)
        if off == "et":
            off = et_off
        a, n1 = conv(m.group(1), m.group(2), m.group(3), off)
        if m.group(4):
            b, n2 = conv(m.group(4), m.group(5), m.group(6), off)
            return f"{a} - {b} ET" + (" (next day)" if n2 else "")
        return f"{a} ET" + (" (next day)" if n1 else "")
    return TIME_ZONE_RX.sub(fix, text)


def send_all(s, title, msg, discord_msg=None, discord_mention_on=True, ack_button=False, channel=None):
    """Phone + Mac get the personal message. Discord only gets discord_msg (if given),
    so the server sees clean, general alerts and none of your personal status pings."""
    title, msg = to_eastern(title), to_eastern(msg)
    discord_msg = to_eastern(discord_msg) if discord_msg else discord_msg
    notify_phone(s, title, msg, ack_button)
    if discord_msg:
        if channel:
            notify_discord(s, discord_msg, discord_mention_on, channel=channel)
        else:
            notify_discord(s, discord_msg, discord_mention_on)
    notify_mac(msg)


# ---------------- Chrome control ----------------
# The watcher works on one specific tab, written as "tab id T of window id W",
# so switching tabs or windows yourself never makes it refresh the wrong page.
NOT_FOUND = "QW_TAB_NOT_FOUND"
CHROME_QUIT = "QW_CHROME_QUIT"
# Talking to Chrome with "tell application" LAUNCHES it if it isn't running. If you quit Chrome
# while watching, that would bring it back invisibly in the background. So every check first
# asks macOS whether Chrome is running, and never starts it.
IF_CHROME = f'if application id "com.google.Chrome" is not running then return "{CHROME_QUIT}"'


def chrome_running():
    out, _ = osa('return application id "com.google.Chrome" is running', timeout=10)
    return out != "false"


def tab_do(win, action, timeout=20):
    """Run an AppleScript action on our tab. Instead of asking Chrome for "tab id X of
    window id Y" (which some Chrome versions don't accept), walk the windows and tabs and
    act on the matching one. In `action`, TAB means the tab and WIN means its window."""
    w, t = win
    act = action.replace("TAB", "(tab i of x)").replace("WIN", "x")
    return osa(f'''-- tab id {t} of window id {w}
        {IF_CHROME}
        tell application "Google Chrome"
            repeat with x in windows
                if (id of x as text) is "{w}" then
                    repeat with i from 1 to (count of tabs of x)
                        if (id of (tab i of x) as text) is "{t}" then
                            {act}
                            return "QW_DONE"
                        end if
                    end repeat
                end if
            end repeat
            return "{NOT_FOUND}"
        end tell''', timeout=timeout)


def tab_get(win, action, timeout=20):
    """Like tab_do, but returns a value. ('', error) if the tab is gone."""
    out, err = tab_do(win, "return " + action, timeout)
    if out in (NOT_FOUND, CHROME_QUIT):
        return "", "tab not found" if out == NOT_FOUND else "Chrome isn't open"
    return out, err


def site_host(url):
    from urllib.parse import urlparse
    host = (urlparse(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


# Never take over a tab where you're in the middle of something.
BUSY_PATHS = ("cart", "checkout", "login", "signin", "sign-in", "account", "payment")


def site_tabs(url):
    """Every open tab on this site that's safe to use, as [(window id, tab id), ...]."""
    host = site_host(url)
    if not host:
        return []
    out, _ = osa(f'''
        {IF_CHROME}
        tell application "Google Chrome"
            set found to ""
            repeat with w in windows
                repeat with t in tabs of w
                    set u to URL of t
                    if u contains "{as_str(host)}" then
                        set found to found & (id of w as text) & "," & (id of t as text) & "," & u & linefeed
                    end if
                end repeat
            end repeat
            return found
        end tell''', timeout=30)
    rows = [l.split(",", 2) for l in out.splitlines()]
    return [(r[0], r[1]) for r in rows if len(r) == 3 and r[0].isdigit() and r[1].isdigit()
            and not any(p in r[2].lower() for p in BUSY_PATHS)]


def readable_tab(url, skip=None):
    """A tab on this site that Chrome lets the app read. Each Chrome profile has its own
    "Allow JavaScript from Apple Events" setting, so a tab in one profile may work when another doesn't."""
    for tab in site_tabs(url):
        if tab == skip:
            continue
        out, err = run_js(tab, "document.title + 'QW'")
        if out and not err:
            return tab
    return None


def find_tab(url, prefer_window=None):
    """An already-open Chrome tab on this site that's safe to use, as (window id, tab id).
    A tab in `prefer_window` (the app's own window) is used even on a sign-in/account page."""
    host = site_host(url)
    if not host:
        return None
    out, _ = osa(f'''
        {IF_CHROME}
        tell application "Google Chrome"
            set found to ""
            repeat with w in windows
                repeat with t in tabs of w
                    set u to URL of t
                    if u contains "{as_str(host)}" then
                        set found to found & (id of w as text) & "," & (id of t as text) & "," & u & linefeed
                    end if
                end repeat
            end repeat
            return found
        end tell''', timeout=30)
    rows = [l.split(",", 2) for l in out.splitlines()]
    rows = [r for r in rows if len(r) == 3 and r[0].isdigit() and r[1].isdigit()]
    if prefer_window:
        for r in rows:
            if r[0] == str(prefer_window):
                return r[0], r[1]
    for r in rows:
        if not any(p in r[2].lower() for p in BUSY_PATHS):
            return r[0], r[1]
    return None


def open_window(url):
    """Use a tab that's already on the site, or open one. Returns ((window, tab), error)."""
    found = readable_tab(url) or find_tab(url)
    if found:
        log("Found an open tab on this site. Using it.")
        return found, ""
    # Long timeout: the first run shows a "control Google Chrome?" permission popup.
    # The new window opens minimized and Chrome is NOT brought to the front, so it stays out
    # of your way. It's still your real Chrome, so a queue spot it gets is yours, and when a
    # queue shows up this same window pops up (see bring_to_front).
    stay_back = load_settings().get("pkc_background", True)
    out, err = osa(f'''
        tell application "Google Chrome"
            {"" if stay_back else "activate"}
            set w to make new window
            set URL of active tab of w to "{as_str(url)}"
            {"try" if stay_back else ""}
            {"set minimized of w to true" if stay_back else ""}
            {"end try" if stay_back else ""}
            return (id of w as text) & "," & (id of active tab of w as text)
        end tell''', timeout=120)
    parts = out.split(",")
    if len(parts) == 2 and all(p.isdigit() for p in parts):
        log("Opened the site in a new Chrome window.")
        return (parts[0], parts[1]), err
    return None, err


def window_exists(win):
    """True if our tab is still open, False if it's gone, None if Chrome didn't answer.
    Walks through Chrome's windows and tabs instead of asking for the tab directly,
    which some Chrome versions answer wrongly."""
    w, t = win
    out, err = osa(f'''-- exists window id {w}: tab id {t} of window id {w}
        {IF_CHROME}
        tell application "Google Chrome"
            repeat with x in windows
                if (id of x as text) is "{w}" then
                    repeat with y in tabs of x
                        if (id of y as text) is "{t}" then return "tab"
                    end repeat
                    return "window"
                end if
            end repeat
            return "none"
        end tell''')
    if out in ("tab", "true"):
        return True
    if out in ("none", "false", "window"):
        return False
    return None      # includes Chrome being quit


REOPENS = []


def relocate(win, s):
    """Our tab seems gone: look for the site in any open tab (our own window first). If there's
    none, open a fresh Pokémon Center window instead of giving up."""
    found = find_tab(s["watch_url"], prefer_window=win[0] if win else None)
    if found:
        if found != win:
            log("Found the Pokémon Center tab again. Still watching.")
        return found
    if not chrome_running():
        return None
    now = time.time()
    REOPENS[:] = [t for t in REOPENS if now - t < 1800]
    if len(REOPENS) >= 3:                  # never pile up windows if something's really wrong
        log("Opened 3 new Pokémon Center windows in 30 minutes already. Not opening another.")
        return None
    REOPENS.append(now)
    log("Couldn't find the Pokémon Center tab. Opening a new one and watching again.")
    again, err = open_window(s["watch_url"])
    if again:
        return again
    log(f"Couldn't open Pokémon Center: {err}")
    return None


def tab_gone(win, stop):
    """True only if the tab is missing twice in a row (Chrome sometimes answers wrong for a moment)."""
    if window_exists(win) is not False:
        return False
    stop.wait(2)
    return window_exists(win) is False


def reload_tab(win):
    tab_do(win, "reload TAB")


def close_tab(win):
    tab_do(win, "close TAB", timeout=10)


def go_to(win, url):
    tab_do(win, f'set URL of TAB to "{as_str(url)}"')


def run_js(win, js):
    """Run JavaScript in the tab. Returns (result, error)."""
    return tab_get(win, f'(execute TAB javascript "{as_str(js)}")', timeout=30)


def wait_for_load(win, stop, min_wait=3, max_wait=25):
    """Wait until Chrome says the page finished loading (or max_wait seconds)."""
    if stop.wait(min_wait):
        return
    waited = min_wait
    while waited < max_wait:
        out, _ = tab_get(win, "loading of TAB")
        if out != "true":
            break
        if stop.wait(1):
            return
        waited += 1
    stop.wait(1)  # let the page draw its text


# Grabs the visible page text, plus a marker if the Imperva waiting-room script is on the page.
PAGE_JS = (
    "(document.body ? document.body.innerText.slice(0, 8000) : '') + "
    "((performance.getEntriesByType('resource').some(function(e){return e.name.indexOf('SWWRGTS') > -1;})"
    " || document.documentElement.innerHTML.indexOf('SWWRGTS') > -1) ? ' " + SCRIPT_MARK + "' : '')"
)


last_js_error = {"text": ""}


def read_page(win):
    url, e1 = tab_get(win, "URL of TAB")
    title, _ = tab_get(win, "title of TAB")
    text, err = run_js(win, PAGE_JS)
    last_js_error["text"] = err or ""
    return url, title, text, bool(err), bool(e1)


def js_error_help(err):
    """Chrome's real reason, in plain words."""
    e = err.lower()
    if "not allowed" in e or "-1743" in e or "not authorized" in e:
        return ("macOS is blocking Guardian Monitor from controlling Chrome. Go to System Settings → Privacy & "
                "Security → Automation, and turn on Google Chrome under Guardian Monitor (and Terminal/python3 if listed).")
    if "turned off" in e or "allow javascript" in e:
        return ("Chrome says reading pages is turned off in the profile this window opened in (often a school or "
                "work profile, which can block it). Fix: open pokemoncenter.com in a tab in your PERSONAL Chrome profile "
                "and leave it open, so Guardian Monitor uses that tab. In that window, check View → Developer → "
                "Allow JavaScript from Apple Events. Then press Stop and Start.")
    if "not allowed" in e or "-1743" in e or "not authorized" in e:
        return ("macOS is blocking Guardian Monitor from controlling Chrome. Go to System Settings → Privacy & "
                "Security → Automation, and turn on Google Chrome under Guardian Monitor (and Terminal/python3 if listed).")
    if "timed out" in e or "-1712" in e:
        return "Chrome took too long to answer. If this keeps showing, restart Chrome."
    return "Chrome wouldn't let it read the page text. Chrome said: " + err.strip()[:200]


def bring_to_front(win):
    if not win:
        return
    tab_do(win, """try
                                set minimized of WIN to false
                            end try
                            set active tab index of WIN to i
                            set index of WIN to 1
                            activate""")


# ---------------- sign-in (Mac Keychain) ----------------
# Your password is kept ONLY in the Mac Keychain. It's never written to the settings
# file, never shown in the app, and only ever typed into the site's own sign-in page.
KC_SERVICE = "Queue Watcher"   # name of the saved login in the Keychain (kept so your login still works)


def kc_service(url):
    return f"{KC_SERVICE}: {site_host(url)}"


def kc_save(url, user, password):
    if not user or not password:
        return False, "Enter both your email/username and password."
    if any(c in user for c in '"\n\r\\'):
        return False, "That username has characters the Keychain can't store this way."
    host = site_host(url)
    if not host:
        return False, "Set the website first."
    # -X takes the password as hex, and the command goes in through stdin,
    # so the password never shows up in your Mac's process list.
    cmd = (f'add-generic-password -U -s "{kc_service(url)}" -a "{user}" '
           f'-l "{kc_service(url)}" -X {password.encode("utf-8").hex()}\n')
    try:
        r = subprocess.run(["security", "-i"], input=cmd, capture_output=True, text=True, timeout=30)
        ok = r.returncode == 0 and "error" not in (r.stdout + r.stderr).lower()
    except Exception as e:
        return False, f"Couldn't reach the Keychain: {e}"
    return (True, f"Saved in your Mac Keychain for {host}.") if ok else (False, "The Keychain said no. Try again.")


def kc_load(url, user):
    if not user:
        return None
    try:
        r = subprocess.run(["security", "find-generic-password", "-s", kc_service(url), "-a", user, "-w"],
                           capture_output=True, text=True, timeout=30)
        return r.stdout.rstrip("\n") if r.returncode == 0 and r.stdout else None
    except Exception:
        return None


def kc_delete(url, user):
    try:
        subprocess.run(["security", "delete-generic-password", "-s", kc_service(url), "-a", user],
                       capture_output=True, text=True, timeout=30)
    except Exception:
        pass


def b64(text):
    import base64
    return base64.b64encode(text.encode("utf-8")).decode()


def fill_login_js(user, password):
    """Fill the visible sign-in form and press its button. Never touches captchas."""
    return ("(function(){"
            "var dec=function(b){return decodeURIComponent(escape(atob(b)));};"
            f"var u=dec('{b64(user)}'),p=dec('{b64(password)}');"
            "var vis=function(e){return e&&e.offsetParent!==null&&!e.disabled;};"
            "var q=function(root,sel){return Array.prototype.slice.call(root.querySelectorAll(sel)).filter(vis);};"
            "var cap=!!document.querySelector('iframe[src*=captcha],iframe[src*=recaptcha],iframe[src*=hcaptcha],"
            "iframe[title*=challenge],.g-recaptcha,.h-captcha,#px-captcha');"
            "var set=function(el,v){var d=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value');"
            "el.focus();d.set.call(el,v);el.dispatchEvent(new Event('input',{bubbles:true}));"
            "el.dispatchEvent(new Event('change',{bubbles:true}));el.blur();};"
            "var pw=q(document,'input[type=password]')[0];"
            "var root=(pw&&pw.form)||document;"
            "var user=q(root,'input[type=email],input[autocomplete=username],input[autocomplete=email],"
            "input[name*=email i],input[name*=user i],input[id*=email i],input[id*=user i]')[0]"
            "||(pw?q(root,'input[type=text]')[0]:null);"
            "if(!pw&&!user)return 'noform';"
            "if(user)set(user,u);if(pw)set(pw,p);"
            "if(cap)return 'captcha';"
            "var btn=q(root,'button[type=submit],input[type=submit],button').filter(function(b){"
            "return /sign ?in|log ?in|continue|next|submit/i.test(b.textContent||b.value||'');})[0];"
            "if(btn){btn.click();return pw?'submitted':'step1';}"
            "if(pw&&pw.form){pw.form.requestSubmit?pw.form.requestSubmit():pw.form.submit();return 'submitted';}"
            "return 'nobutton';})()")


def signed_in_state(text, s):
    """'in', 'out', or 'unknown', based on your sign-in words."""
    blob = normalize(text)
    if find_word(blob, split_words(s["signed_in_words"])):
        return "in"
    if find_word(blob, split_words(s["signed_out_words"])):
        return "out"
    return "unknown"


def try_sign_in(win, s, stop):
    """Go to the sign-in page, fill it from the Keychain, then come back. Returns a short result."""
    if not s["login_url"]:
        return "no sign-in page set"
    password = kc_load(s["watch_url"], s["login_user"])
    if not password:
        return "no saved login"
    log("Signing in with your saved login...")
    go_to(win, s["login_url"])
    wait_for_load(win, stop)
    for step in range(2):                      # some sites ask for email first, then password
        result, err = run_js(win, fill_login_js(s["login_user"], password))
        if err:
            return "couldn't fill the form"
        if result == "captcha":
            return wait_for_you(win, s, stop)
        if result in ("noform", "nobutton"):
            return {"captcha": "the site showed a captcha",
                    "noform": "couldn't find the sign-in form",
                    "nobutton": "couldn't find the sign-in button"}[result]
        wait_for_load(win, stop)
        if result == "submitted":
            break
    password = None
    go_to(win, s["watch_url"])
    wait_for_load(win, stop)
    return "done"


CAPTCHA_WAIT = 10 * 60


def wait_for_you(win, s, stop):
    """A captcha showed up. Your login is filled in; bring the window up, ping you, and wait
    (up to 10 minutes) for you to solve it and press Sign in yourself."""
    host = site_host(s["watch_url"])
    bring_to_front(win)
    log("Captcha on the sign-in page. Waiting for you to solve it.")
    send_all(s, "Captcha: finish signing in",
             f"{host} wants a captcha. Your login is filled in: go to the Chrome window, "
             "solve it, and press Sign in. Watching resumes after.")
    login_path = s["login_url"].split("://", 1)[-1].split("?")[0].rstrip("/").lower()
    end = time.time() + CAPTCHA_WAIT
    while time.time() < end and not stop.is_set():
        if stop.wait(5):
            break
        url, _, text, _, failed = read_page(win)
        if failed:
            continue
        here = url.split("://", 1)[-1].split("?")[0].rstrip("/").lower()
        if here != login_path or signed_in_state(text, s) == "in":
            log("Looks like you finished signing in. Back to watching.")
            go_to(win, s["watch_url"])
            wait_for_load(win, stop)
            return "done"
    go_to(win, s["watch_url"])
    wait_for_load(win, stop)
    return "the captcha wasn't finished in time"


def check_sign_in(win, s, text, stop):
    """Once per run: if you look signed out, sign in (if set up) or ping you."""
    if not s["check_signed_in"] or signed_in_state(text, s) != "out":
        return
    host = site_host(s["watch_url"])
    if s["auto_login"] and s["login_user"]:
        result = try_sign_in(win, s, stop)
        if result == "done":
            _, _, text2, _, _ = read_page(win)
            if signed_in_state(text2, s) != "out":
                log(f"Signed in to {host}.")
                notify_mac(f"Signed you in to {host}.")
                return
            result = "still looks signed out afterwards"
        log(f"Automatic sign-in didn't work: {result}.")
        if result != "still looks signed out afterwards":
            go_to(win, s["watch_url"])             # don't leave the tab sitting on the sign-in page
            wait_for_load(win, stop)
        send_all(s, "Sign in needed", f"Couldn't sign you in to {host} ({result}). "
                 "Please sign in in the Chrome window before the drop. Still watching.")
    else:
        log(f"Looks signed out of {host}.")
        send_all(s, "Sign in needed", f"You look signed out of {host}. "
                 "Sign in in the Chrome window before the drop. Still watching.")


def normalize(text):
    return text.lower().replace("’", "'").replace("‘", "'")


def find_word(blob, words):
    for w in words:
        if w and w in blob:
            return w
    return None


def split_words(text):
    return [normalize(w.strip()) for w in text.split(",") if w.strip()]


# ---------------- the watcher ----------------
def stopped_unexpectedly(s, why):
    """Watcher stopped on its own: tell the UI AND ping you, so you know it's not watching."""
    log(f"Stopped: {why}")
    set_state(status="error", message=f"Stopped: {why} Press Start to try again.")
    send_all(s, "Guardian Monitor stopped", f"Guardian Monitor stopped: {why} It is NOT watching right now.")


def queue_alerts(s, win, word):
    """Ping now, then repeat every minute until you press "I'm on it"
    (in the app OR on the phone notification), or Stop."""
    ack_event.clear()
    since = time.time() - 2
    test = s.get("_test")
    log(f'{"TEST " if test else ""}QUEUE FOUND (matched "{word}")! Refreshing is OFF.')
    set_state(status="found",
              message=("TEST QUEUE FOUND! The drill worked. Press I'm on it, then close the test window."
                       if test else "QUEUE FOUND! Go to the Chrome window. Refreshing is OFF."))
    bring_to_front(win)
    for i in range(REPEAT_ALERTS):
        extra = "" if i == 0 else f" (reminder {i})"
        # Discord gets ONE general alert; the reminders only go to your phone and Mac.
        if test:   # drill: clearly marked, and never @'s anyone in Discord
            send_all(s, "TEST: queue alert", f"TEST: Guardian Monitor drill. Alerts are working!{extra}",
                     None if i else "\U0001F9EA Test from Guardian Monitor: queue alerts are working "
                     "(this was a drill, no real queue).", discord_mention_on=False, ack_button=True)
        else:
            server_msg = None if i else discord_text(s, "discord_queue_msg")
            send_all(s, "Pokemon Center queue is UP!",
                     f'Pokemon Center queue is UP! Get to your PC.{extra} (matched: "{word}")',
                     server_msg, ack_button=True)
        # Wait for the next reminder, checking the phone button every few seconds.
        deadline = time.time() + REPEAT_EVERY
        while time.time() < deadline and not ack_event.is_set() and not stop_event.is_set():
            if phone_acked(s, since):
                log("You tapped I'm on it on your phone.")
                ack_event.set()
                break
            ack_event.wait(min(PHONE_ACK_CHECK, max(0.1, deadline - time.time())))
        if ack_event.is_set() or stop_event.is_set():
            break
    if ack_event.is_set():
        log("Queue alerts stopped.")
        set_state(message="QUEUE FOUND. Alerts off. Good luck! Press Start to watch again.")


BACKUP_HELP = ("Backup mode: Chrome blocks reading pages here (likely your school profile's settings), so a "
               "hidden reader watches Pokémon Center instead. Your Chrome tab only refreshes when a queue "
               "shows up, which puts you in line. Auto sign-in is off in this mode, so stay signed in.")


def watch_hidden(s, stop, win, queue_words, block_words, headsup_words):
    """Backup mode for when Chrome won't let the app read pages. A hidden reader watches the
    site; your real Chrome tab is refreshed ONCE, when a queue appears, so the spot is yours."""
    log("Chrome blocks reading pages, so switching to backup mode (hidden reader).")
    set_state(status="watching", message="Watching for a queue (backup mode)...", js_warning=True,
              js_help=BACKUP_HELP)
    misses, last_headsup, err_streak = 0, 0, 0
    while not stop.is_set():
        try:
            d = read_headless("pkc", s["watch_url"])
            misses = 0
        except Exception as e:
            misses += 1
            log(f"Backup reader couldn't load the page ({misses}/{MAX_READ_FAILS}): {e}")
            if misses >= MAX_READ_FAILS:
                stopped_unexpectedly(s, "the backup reader couldn't load Pokémon Center.")
                return
            if stop.wait(random.randint(s["min_wait"], s["max_wait"])):
                break
            continue
        with lock:
            state["checks"] += 1
            state["last_check"] = datetime.now().strftime("%I:%M:%S %p")
            checks = state["checks"]
        blob = normalize(f"{d.get('url', '')}\n{d.get('title', '')}\n{d.get('text', '')}")
        word = find_word(blob, queue_words)
        if word:
            if word == SCRIPT_MARK:
                word = "waiting-room script on page"
            log("Backup reader saw a queue. Joining it in your Chrome tab.")
            if window_exists(win) is not True if win else True:
                win = relocate(win, s) if win else None
                if not win:
                    win, _ = open_window(s["watch_url"])
            if win:
                go_to(win, s["watch_url"])          # one refresh puts YOU in line, in your Chrome
            queue_alerts(s, win, word)
            return
        word = find_word(blob, block_words)
        if word:
            wait = error_cooldown(word, err_streak)
            log(f'Backup reader got an error page ("{word}"). Resting {mins(wait)}, then trying again.')
            if err_streak == 0:
                info_ping(s, "⚠️ Pokémon Center error page",
                          f'It showed "{word}". Trying again at {short_time(time.time() + wait)} ({mins(wait)}).\n'
                          "Nothing to do. You'll get a ping when it's watching again.")
            err_streak += 1
            if stop.wait(wait):
                break
            continue
        if err_streak:
            info_ping(s, "✅ Watching again", "Pokémon Center loaded fine again. Back to checking for a queue.")
        err_streak = 0
        word = find_word(blob, headsup_words)
        if word:
            log(f'Heads up: site says "{word}". Still watching.')
            if time.time() - last_headsup > HEADSUP_COOLDOWN:
                last_headsup = time.time()
                send_all(s, "Pokemon Center heads up",
                         f'Pokemon Center says "{word}". A drop may be starting. Still watching for the queue.',
                         discord_text(s, "discord_headsup_msg"), discord_mention_on=False)
        else:
            log(f"Check #{checks}: no queue (backup mode).")
        if stop.wait(random.randint(s["min_wait"], s["max_wait"])):
            break
    set_state(status="idle", message="Stopped. Press Start to watch again.")
    log("Stopped by you.")


# Error / block pages: never give up. Close the tab, rest (so it doesn't look like a bot hammering
# the site, which is what gets soft bans), then open a fresh tab and keep watching.
# The rest gets longer if it keeps happening, and resets after a normal check.
HARD_BLOCK = ("incapsula incident", "access denied", "are you a robot", "pardon our interruption")
SOFT_COOLDOWN = (120, 300, 600, 1200, 1800)        # 2, 5, 10, 20, then 30 minutes
HARD_COOLDOWN = (600, 1200, 1800)                  # bot checks: 10, 20, then 30 minutes


def error_cooldown(word, streak):
    steps = HARD_COOLDOWN if word in HARD_BLOCK else SOFT_COOLDOWN
    return steps[min(streak, len(steps) - 1)]


def mins(sec):
    if sec < 60:
        return f"{int(sec)} seconds"
    m = round(sec / 60)
    return f"{m} minute" + ("" if m == 1 else "s")


def recover_from_error(s, stop, win, word, streak):
    """Close the error tab, rest, open a fresh one. Returns the new tab (None if stopped/couldn't)."""
    wait = error_cooldown(word, streak)
    log(f'Pokémon Center showed an error page ("{word}"). Closing the tab, resting {mins(wait)}, '
        "then opening a fresh one.")
    set_state(message=f"Error page from Pokémon Center. Resting {mins(wait)}, then reopening. Still watching.")
    back_at = short_time(time.time() + wait)
    pinged = streak in (0, 3)
    if streak == 0:
        info_ping(s, "⚠️ Pokémon Center error page",
                  f'It showed "{word}". Closed that tab; a fresh one opens at {back_at} ({mins(wait)}).\n'
                  "Nothing to do. You'll get a ping when it's watching again.")
    elif streak == 3:
        info_ping(s, "⚠️ Pokémon Center keeps erroring",
                  f"Still retrying, waiting longer each time (up to {mins(error_cooldown(word, 9))}). "
                  f"Next try at {back_at}. A queue still alerts you right away.")
    if win:
        close_tab(win)
    if stop.wait(wait):
        return None
    again = find_tab(s["watch_url"])
    if not again:
        again, _ = open_window(s["watch_url"])
    if again:
        log("Opened a fresh Pokémon Center tab. Watching again.")
        set_state(message="Watching for a queue...")
        if pinged:
            info_ping(s, "✅ Watching again", "Opened a fresh Pokémon Center tab after the error. "
                      "Back to checking for a queue.")
    return again


def watch(s, stop):
    queue_words = split_words(s["queue_words"])
    block_words = split_words(s["block_words"])
    headsup_words = split_words(s["headsup_words"])
    if s["use_script_check"]:
        queue_words.insert(0, SCRIPT_MARK)
    last_headsup = 0
    caffeinate = None
    try:
        # -w: caffeinate quits by itself when this app quits, so your Mac can sleep again.
        caffeinate = subprocess.Popen(["caffeinate", "-i", "-w", str(os.getpid())])
    except Exception:
        pass

    try:
        log("Looking for the site in Chrome...")
        win, err = open_window(s["watch_url"])
        if not win:
            log(f"Chrome error: {err}")
            set_state(status="error",
                      message="Couldn't control Chrome. Make sure Google Chrome is installed "
                              "and you clicked Allow on the permission popup.")
            return

        set_state(status="watching", message="Watching for a queue...", checks=0)
        warned = False
        js_fails = 0
        fails = 0
        login_checked = False
        error_streak = 0

        while not stop.is_set():
            wait_for_load(win, stop)
            if stop.is_set():
                break

            if not chrome_running():
                log("Chrome was quit. Pausing until you open Chrome again (it won't reopen it for you).")
                set_state(message="Paused: Chrome isn't open. Open Chrome and it picks back up.")
                while not stop.is_set() and not chrome_running():
                    stop.wait(10)
                if stop.is_set():
                    break
                stop.wait(5)                      # give Chrome a moment to restore its windows
                again = relocate(win, s)
                if not again:
                    again, _ = open_window(s["watch_url"])
                if not again:
                    stopped_unexpectedly(s, "couldn't find or open Pokémon Center after Chrome reopened.")
                    return
                win = again
                log("Chrome is back. Watching again.")
                set_state(message="Watching for a queue...")
                continue

            exists = window_exists(win)
            if exists is False and tab_gone(win, stop):
                again = relocate(win, s)
                if not again:
                    stopped_unexpectedly(s, "no Pokémon Center tab is open in Chrome, and it couldn't open a new one.")
                    return
                win, exists = again, True
                wait_for_load(win, stop)
            elif exists is False:
                exists = True

            url, title, text, text_blocked, url_failed = read_page(win)
            if exists is None or url_failed:
                fails += 1
                log(f"Couldn't read Chrome ({fails}/{MAX_READ_FAILS}).")
                if fails >= MAX_READ_FAILS:
                    stopped_unexpectedly(s, "Chrome stopped responding or was quit.")
                    return
            else:
                fails = 0
                if text_blocked:
                    js_fails += 1
                    if js_fails >= 2 and "turned off" in last_js_error["text"].lower():
                        other = readable_tab(s["watch_url"], skip=win)
                        if other:                 # a Pokémon Center tab in a Chrome profile that allows it
                            log("This tab's Chrome profile blocks reading pages. Switched to a Pokémon Center "
                                "tab in your other Chrome profile, which allows it.")
                            win, js_fails = other, 0
                            set_state(js_warning=False)
                            continue
                    if js_fails >= 2 and headless_available() and \
                            "turned off" in last_js_error["text"].lower():
                        return watch_hidden(s, stop, win, queue_words, block_words, headsup_words)
                    if js_fails >= 2 and not warned:      # one hiccup (page still loading) isn't a problem
                        warned = True
                        err = last_js_error["text"]
                        set_state(js_warning=True, js_help=js_error_help(err))
                        log(f"Can't read page text. Chrome said: {err.strip()[:200]}")
                else:
                    js_fails = 0
                    warned = False
                    set_state(js_warning=False)

                with lock:
                    state["checks"] += 1
                    state["last_check"] = datetime.now().strftime("%I:%M:%S %p")
                    checks = state["checks"]

                blob = normalize(f"{url}\n{title}\n{text}")

                word = find_word(blob, queue_words)
                if word:
                    if word == SCRIPT_MARK:
                        word = "waiting-room script on page"
                    queue_alerts(s, win, word)
                    return

                word = find_word(blob, block_words)
                if word:
                    again = recover_from_error(s, stop, win, word, error_streak)
                    error_streak += 1
                    if stop.is_set():
                        break
                    if not again:
                        stopped_unexpectedly(s, "couldn't open a fresh Pokémon Center tab after an error page.")
                        return
                    win = again
                    continue
                error_streak = 0

                word = find_word(blob, headsup_words)
                if word:
                    log(f'Heads up: site says "{word}". A drop may be starting. Still watching.')
                    if time.time() - last_headsup > HEADSUP_COOLDOWN:
                        last_headsup = time.time()
                        send_all(s, "Pokemon Center heads up",
                                 f'Pokemon Center says "{word}". A drop may be starting. '
                                 "Still watching for the queue.",
                                 discord_text(s, "discord_headsup_msg"),
                                 discord_mention_on=False)
                else:
                    log(f"Check #{checks}: no queue.")
                if not login_checked and not text_blocked:
                    login_checked = True
                    check_sign_in(win, s, text, stop)

            wait = int(random.randint(s["min_wait"], s["max_wait"]) * (2 if health["level"] in ("warm", "hot") else 1))
            if stop.wait(wait):
                break
            if tab_gone(win, stop):
                again = relocate(win, s)
                if not again:
                    stopped_unexpectedly(s, "no Pokémon Center tab is open in Chrome, and it couldn't open a new one.")
                    return
                win = again
                continue                           # a fresh tab just loaded; no need to refresh it
            reload_tab(win)

        set_state(status="idle", message="Stopped. Press Start to watch again.")
        log("Stopped by you.")
    except Exception as e:
        stopped_unexpectedly(s, f"unexpected error ({e}).")
    finally:
        if caffeinate:
            caffeinate.terminate()



# ---------------- releases & drops news ----------------
import html as _html
import re as _re
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from datetime import timedelta

NEWS_FILE = os.path.join(DATA_DIR, "news.json")
NEWS_EVERY = 30 * 60          # refresh in the background every 30 minutes
news_lock = threading.Lock()
news_busy = threading.Lock()

MONTHS = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}
NEWS_CACHE = os.path.join(DATA_DIR, "news_cache.json")
NEWS_DAYS_AHEAD = 400            # how far ahead dates are kept
NEWS_DAYS_BACK = 7               # "just released" window
MAX_ARTICLES = 10                # full articles read per source per refresh (each is read once, then remembered)


def parse_feeds_setting(text):
    feeds = []
    for line in str(text).splitlines():
        line = line.strip()
        if not line:
            continue
        name, _, url = line.rpartition("|") if "|" in line else ("", "", line)
        url = url.strip()
        if url.startswith(("http://", "https://")):
            feeds.append((name.strip() or url.split("/")[2], url))
    return feeds[:12]


def strip_tags(text):
    return _re.sub(r"\s+", " ", _html.unescape(_re.sub(r"<[^>]+>", " ", text or ""))).strip()


def item_date(text):
    try:
        return parsedate_to_datetime(text).astimezone()
    except Exception:
        pass
    try:
        return datetime.fromisoformat(text.strip().replace("Z", "+00:00")).astimezone()
    except Exception:
        return None


# ---- getting a page or feed (like a normal browser; a hidden Chrome if the site blocks that) ----
def browser_headers():
    try:
        ua = chrome_user_agent()
    except Exception:
        ua = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/141.0.0.0 Safari/537.36")
    return {"User-Agent": ua, "Accept-Language": "en-US,en;q=0.9",
            "Accept": "text/html,application/xhtml+xml,application/rss+xml,application/atom+xml,"
                      "application/xml;q=0.9,*/*;q=0.8"}


def why_failed(e):
    if isinstance(e, urllib.error.HTTPError):
        return {403: "it blocked the reader", 404: "that link doesn't exist anymore",
                429: "it's busy right now"}.get(e.code, f"the site had a problem ({e.code})" if e.code >= 500
                                                 else f"it said no ({e.code})")
    if isinstance(e, urllib.error.URLError):
        return "couldn't reach it"
    return "couldn't read it"


def get_url(url, slug="news", hidden_ok=True):
    last = None
    for attempt in range(2):
        try:
            req = urllib.request.Request(url, headers=browser_headers())
            with fetch(req, timeout=15) as r:
                raw = r.read(4 * 1024 * 1024)
                return raw.decode(r.headers.get_content_charset() or "utf-8", "replace")
        except urllib.error.HTTPError as e:
            last = e
            if e.code in (429, 503) and attempt == 0:
                time.sleep(2)
                continue
            break
        except Exception as e:
            last = e
            break
    if hidden_ok and headless_available():       # sites that block simple readers usually let Chrome in
        try:
            out = dump_dom(slug, url)
            if out and len(out) > 300 and not _re.search(r"just a moment|attention required|access denied",
                                                         out[:3000], _re.I):
                return out
        except Exception:
            pass
    raise last or RuntimeError("couldn't load")


# ---- reading feeds (forgiving: works on plain XML and on Chrome's view of a feed) ----
_ITEM = _re.compile(r"<(item|entry)\b[^>]*>(.*?)</\1\s*>", _re.S | _re.I)


def _unwrap(s):
    s = s.strip()
    m = _re.match(r"<!\[CDATA\[(.*?)\]\]>\s*$", s, _re.S)
    if m:
        return m.group(1)
    return _html.unescape(s)


def _tag(block, *names):
    for n in names:
        m = _re.search(r"<" + _re.escape(n) + r"\b[^>]*>(.*?)</" + _re.escape(n) + r"\s*>", block, _re.S | _re.I)
        if m:
            return _unwrap(m.group(1))
    return ""


def _link(block):
    m = (_re.search(r"<link\b[^>]*rel=[\"']alternate[\"'][^>]*href=[\"']([^\"']+)", block, _re.I) or
         _re.search(r"<link\b[^>]*href=[\"']([^\"']+)", block, _re.I))
    if m:
        return _html.unescape(m.group(1)).strip()
    return strip_tags(_tag(block, "link")).strip()


def feed_items(raw):
    """[(title, link, published, html)] if raw is a feed, else None."""
    if "webkit-xml-viewer-source-xml" in raw:                 # Chrome showing a feed
        raw = raw.split("webkit-xml-viewer-source-xml", 1)[1]
    elif _re.search(r"<pre[^>]*>\s*&lt;\?xml|<pre[^>]*>\s*&lt;(rss|feed)", raw[:5000], _re.I):
        raw = _html.unescape(_re.sub(r"</?pre[^>]*>", "", raw))
    if not _re.search(r"<(rss|feed|rdf:rdf)\b", raw[:5000], _re.I) or not _ITEM.search(raw):
        return None
    out = []
    for m in _ITEM.finditer(raw):
        b = m.group(2)
        title = strip_tags(_tag(b, "title"))
        link = _link(b)
        pub = item_date(strip_tags(_tag(b, "pubDate", "published", "updated", "dc:date")))
        body = _tag(b, "content:encoded", "description", "summary", "content")
        if title and link.startswith(("http://", "https://")):
            out.append((title, link, pub, body))
    return out


# ---- reading a page into short pieces of text ----
SKIP_BLOCK = {"nav", "header", "footer", "aside", "form", "button", "select", "script", "style", "noscript",
              "template", "svg", "head", "iframe"}
WRAP_TAGS = {"div", "section", "article", "main", "table", "tbody", "thead", "ul", "ol", "dl"}


def _parts(n, cache):
    """Each child of a list item or table row as its own piece: 'English', 'In 21 days', 'Delta Reign'."""
    out = []
    for k in n.kids:
        if isinstance(k, str):
            if k.strip():
                out.append(_re.sub(r"\s+", " ", k).strip())
        elif k.tag not in SKIP_TAGS and not _hidden(k):
            if k.kids and all(isinstance(x, str) for x in k.kids) or k.tag in ("a", "b", "strong", "em", "i", "span", "time"):
                t = _re.sub(r"\s+", " ", _text(k, cache)).strip()
                if t:
                    out.append(t)
            else:
                out.extend(_parts(k, cache))
    return out


def html_segments(html):
    """[(heading, text)]: each paragraph, list item and table row, with the heading it sits under."""
    t = _Tree()
    try:
        t.feed(html)
    except Exception:
        pass
    root = t.root
    arts = [n for n in _iter(root) if n.tag == "article"]
    base = arts[0] if len(arts) == 1 else next((n for n in _iter(root) if n.tag == "body"), root)
    segs, buf, cache, st = [], [], {}, {"h": ""}

    def flush():
        s = _re.sub(r"\s+", " ", "".join(buf)).strip()
        buf.clear()
        if s:
            segs.append((st["h"], s))

    def walk(x):
        for k in x.kids:
            if isinstance(k, str):
                buf.append(k)
                continue
            if k.tag in SKIP_BLOCK or _hidden(k):
                continue
            if k.tag in ("li", "tr"):
                flush()
                s = " · ".join(p for p in _parts(k, cache) if p)
                if s:
                    segs.append((st["h"], s))
                continue
            if k.tag in ("h1", "h2", "h3"):
                flush()
                h = _re.sub(r"\s+", " ", _text(k, cache)).strip()
                if h:
                    st["h"] = h
                    segs.append((h, h))
                continue
            blk = k.tag in BLOCK_TAGS or k.tag in WRAP_TAGS
            if blk:
                flush()
            walk(k)
            if blk:
                flush()

    walk(base)
    flush()
    return segs


# ---- finding "X drops on Y" ----
_MON = (r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|"
        r"sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)")
_SUF = r"(?:st|nd|rd|th)?"
D_MDY = _re.compile(r"\b" + _MON + r"\.?\s+(\d{1,2})" + _SUF + r"\b(?!\s*(?:pm|am|:))(?:,?\s+(20\d\d))?", _re.I)
D_DMY = _re.compile(r"\b(\d{1,2})" + _SUF + r"\s+(?:of\s+)?" + _MON + r"\b\.?(?:,?\s+(20\d\d))?", _re.I)
D_MY = _re.compile(r"\b(?:(early|mid|late)[\s-]+)?" + _MON + r"\.?,?\s+(20\d\d)\b", _re.I)
D_SEASON = _re.compile(r"\b(early|mid|late|spring|summer|fall|autumn|winter|q[1-4])[\s-]+(20\d\d)\b", _re.I)
SEASON_MONTH = {"early": 2, "mid": 6, "late": 11, "spring": 4, "summer": 7, "fall": 10, "autumn": 10,
                "winter": 1, "q1": 2, "q2": 5, "q3": 8, "q4": 11}

PTYPES = [
    ("Pokémon Center Elite Trainer Box", r"pok[eé]mon center elite trainer box(?:es)?|pok[eé]mon center etbs?|pc etbs?"),
    ("Ultra-Premium Collection", r"ultra[- ]premium collections?|upcs?"),
    ("Super-Premium Collection", r"super[- ]premium collections?|spcs?"),
    ("Elite Trainer Box", r"elite trainer box(?:es)?|etbs?"),
    ("Booster Bundle", r"booster bundles?"),
    ("Half Booster Box", r"half booster box(?:es)?"),
    ("Booster Box", r"booster box(?:es)?|booster displays?"),
    ("Build & Battle Box", r"build (?:&|and) battle (?:box(?:es)?|kits?)"),
    ("Build & Battle Stadium", r"build (?:&|and) battle stadiums?"),
    ("Mini Tin Display", r"mini tin displays?"),
    ("Mini Tin", r"mini tins?"),
    ("Premium Checklane Blister", r"premium checklane blisters?"),
    ("Checklane Blister", r"checklane blisters?"),
    ("3-Pack Blister", r"(?:three|3)[- ]pack blisters?"),
    ("2-Pack Blister", r"(?:two|2)[- ]pack blisters?"),
    ("Sleeved Booster", r"sleeved booster(?:s| packs?)?"),
    ("Booster Pack", r"booster packs?"),
    ("Premium Collection", r"premium (?:figure |poster |playmat )?collections?"),
    ("Collection", r"(?:poster|binder|figure|illustration|sticker|pin|knock ?out|surprise|treasure|ex|v) collections?|collection box(?:es)?|collections?"),
    ("Tin", r"tins?"),
    ("ex Box", r"ex box(?:es)?"),
    ("Battle Deck", r"(?:league )?battle decks?|starter decks?|ex battle decks?"),
    ("Trainer's Toolkit", r"trainer'?s toolkits?"),
    ("Calendar", r"advent calendars?|holiday calendars?"),
    ("Collector Chest", r"collector'?s? chests?"),
    ("Blister", r"blisters?"),
    ("Box", r"box(?:es)?"),
    ("set", r"expansion|set|sets"),
]
PTYPE_RX = _re.compile(r"\b(" + "|".join(f"(?P<t{i}>{p})" for i, (_, p) in enumerate(PTYPES)) + r")\b", _re.I)
NAME_WORD = _re.compile(r"^(?:[A-Z0-9Pp][\w'’é.&!:+\-—–]*|ex|EX|GX|V|VMAX|VSTAR|x|X|&|and|of|the|de|Z)$")
NOT_A_NAME = {"the", "a", "an", "this", "that", "these", "new", "next", "upcoming", "english", "japanese", "first",
              "every", "each", "all", "its", "their", "our", "your", "two", "three", "both", "latest", "special",
              "and", "of", "in", "on", "at", "for", "with", "from", "by", "to", "as", "or", "plus", "also", "more",
              "other", "standard", "main", "base", "full", "entire", "whole", "regular", "official", "classic",
              "exclusive", "retail", "which", "one", "another", "it", "has", "will", "is", "are", "was"}
BOILER = _re.compile(r"^(?:pok[eé]mon (?:tcg|trading card game)\s*[:\-—–]?\s*|pok[eé]mon\s+|tcg\s*[:\-—–]?\s*|"
                     r"(?:mega evolution|scarlet (?:&|and) violet|sword (?:&|and) shield)\s*(?:series\s*)?[—–:\-]+\s*)",
                     _re.I)
JAPAN = _re.compile(r"\b(japan(?:ese)?|chinese|korean|thai|indonesian|simplified|traditional chinese)\b", _re.I)
ENGLISH = _re.compile(r"\b(english|international|u\.?s\.?|north america|worldwide|global)\b", _re.I)
HEDGE = _re.compile(r"\b(expect(?:ed|s)?|rumou?r(?:ed|s)?|leak(?:ed|s)?|reportedly|likely|probabl[ey]|listings?|"
                    r"listed|tentative(?:ly)?|estimated|speculat\w*|could|might|may be|possibly|unconfirmed|"
                    r"not (?:been )?confirmed|tba|tbd|placeholder|supposedly|allegedly|predict\w*|anticipated|"
                    r"projected|guess)\b", _re.I)
SKIP_DATE = _re.compile(r"\b(legal|tournament|tcg live|rotation|championships?|worlds|regionals?|livestream|stream|"
                        r"posted|published|updated|card reveals?|revealed on|reveal(?:s|ed)? (?:on|begin|start)|"
                        r"japan(?:ese)? release|in japan|anime|episode|video game|app|tcg pocket|pocket)\b", _re.I)
RELEASE_WORD = _re.compile(r"\b(releas\w*|pre-?releas\w*|arriv\w*|launch\w*|hits? shelves|hitting shelves|out|available|drops?|"
                           r"dropping|on sale|street date|comes? out|coming|debut\w*|lands?)\b", _re.I)
STORES = _re.compile(r"\b(target|walmart|costco|best buy|gamestop|sam'?s club|barnes (?:&|and) noble|meijer|"
                     r"walgreens|cvs|five below|amazon|dollar general|kohl'?s|game stores?|local game stores?|lgs)\b",
                     _re.I)
STORE_LABEL = {"lgs": "Local game stores", "game store": "Local game stores", "game stores": "Local game stores",
               "local game store": "Local game stores", "local game stores": "Local game stores"}
BUILTIN_SETS = ["Delta Reign", "30th Celebration", "Pitch Black", "Phantasmal Flames", "Ascended Heroes",
                "Perfect Order", "Chaos Rising", "Mega Evolution", "Destined Rivals", "Black Bolt", "White Flare",
                "Prismatic Evolutions", "Journey Together", "Surging Sparks", "Stellar Crown"]
SET_PATTERNS = [
    _re.compile(r"(?:Mega Evolution|Scarlet (?:&|and) Violet|Sword (?:&|and) Shield)\s*[—–:-]+\s*"
                r"((?:[A-Z0-9][\w'’é]*)(?:\s+(?:[A-Z0-9][\w'’é]*|of|the|&)){0,3})"),
    _re.compile(r"\b((?:[A-Z0-9][\w'’é]*)(?:\s+(?:[A-Z0-9][\w'’é]*|of|the|&)){0,3})\s+(?:expansion|set)\b"),
]


def _ymd(y, m, d):
    try:
        return datetime(y, m, d).date()
    except ValueError:
        return None


def _infer_year(m, d, base):
    """A date without a year: the next time that day comes around (from when the post was written)."""
    when = _ymd(base.year, m, d)
    if when and when < base - timedelta(days=45):
        when = _ymd(base.year + 1, m, d)
    return when


def find_dates(text, base):
    """[(start, end, date, exact)] for dates in the text. exact=False for 'January 2027' or 'early 2027'."""
    out, taken = [], []

    def free(a, b):
        return all(b <= x or a >= y for x, y in taken)

    for rx, kind in ((D_MDY, "mdy"), (D_DMY, "dmy"), (D_MY, "my"), (D_SEASON, "season")):
        for m in rx.finditer(text):
            a, b = m.span()
            if not free(a, b):
                continue
            g = m.groups()
            try:
                if kind == "mdy":
                    if g[0].lower() == "may" and not text[a].isupper():
                        continue
                    mon, d = MONTHS[g[0][:3].lower()], int(g[1])
                    when = _ymd(int(g[2]), mon, d) if g[2] else _infer_year(mon, d, base)
                    exact = True
                elif kind == "dmy":
                    if g[1].lower() == "may" and not m.group(0).split()[-1][0].isupper():
                        continue
                    mon, d = MONTHS[g[1][:3].lower()], int(g[0])
                    year = g[2]
                    if not year:      # "24 Oct to 1 Nov 2026": borrow the year from the end of the range
                        y2 = _re.match(r"\s*(?:-|–|to|through|until)\s*\d{1,2}" + _SUF + r"\s+" + _MON + r"\.?,?\s+(20\d\d)",
                                       text[b:b + 30], _re.I)
                        year = y2.group(2) if y2 else None
                    when = _ymd(int(year), mon, d) if year else _infer_year(mon, d, base)
                    exact = True
                elif kind == "my":
                    if g[1].lower() == "may" and not _re.search(r"\bMay\b", m.group(0)):
                        continue
                    mon, y = MONTHS[g[1][:3].lower()], int(g[2])
                    when = _ymd(y, mon, {"early": 5, "mid": 15, "late": 25}.get((g[0] or "").lower(), 1))
                    exact = False
                else:
                    when = _ymd(int(g[1]), SEASON_MONTH[g[0].lower()], 1)
                    exact = False
            except Exception:
                continue
            if when:
                taken.append((a, b))
                out.append((a, b, when, exact, m.group(0)))
    out.sort()
    # "October 24 to November 1": the event starts on the first date; drop the end of the range
    return [d for d in out if not _re.search(r"(?:\bto|through|until|thru|-|–)\s*$", text[max(0, d[0] - 12):d[0]])
            or not any(o[1] <= d[0] and d[0] - o[1] <= 12 for o in out)]


def find_sets(text):
    found = []
    for rx in SET_PATTERNS:
        for m in rx.finditer(text):
            name = m.group(1).strip(" .,:;")
            words = name.split()
            while words and words[0].lower() in NOT_A_NAME | {"pokémon", "pokemon", "tcg", "mega"}:
                words = words[1:]
            name = " ".join(words)
            if 3 <= len(name) <= 40 and name.lower() not in NOT_A_NAME and not _re.search(r"\d{4}", name):
                found.append(name)
    return found


def canon_type(m):
    for i, (label, _) in enumerate(PTYPES):
        if m.group(f"t{i}"):
            return label
    return None


def product_name(text, m, known_sets, fallback_set, start=0):
    """Build 'Delta Reign Elite Trainer Box' from the words in front of the product type
    (only back to the previous product, so 'the ETB and Booster Bundle' gives two products)."""
    label = canon_type(m)
    words = text[start:m.start()].split()
    name = []
    for w in reversed(words[-9:]):
        core = w.strip(",;()\"“”")
        if w.endswith((",", ";", ")")) and name:
            break
        if not NAME_WORD.match(core):
            break
        name.insert(0, core)
    while name and (name[0].lower() in NOT_A_NAME or _re.search(r"exclusive$", name[0], _re.I)
                    or STORES.fullmatch(name[0].rstrip("'s’"))):
        name = name[1:]
    while name and name[-1].lower() in ("and", "&", "of", "the", "x"):
        name = name[:-1]
    nm = " ".join(name)
    for _ in range(3):
        nm = BOILER.sub("", nm).strip(" :—–-")
    nm = _re.sub(r"['’]s$", "", nm).strip()
    if not nm and label in ("Calendar", "Collector Chest", "Trainer's Toolkit"):
        return m.group(0).title().replace("'S", "'s")          # "Holiday Calendar" names itself
    if label in ("Box", "Collection", "Tin", "Blister") and not nm:
        return None                                    # "a box" / "the collection": too vague
    if label == "set":
        if not nm or nm.lower() in NOT_A_NAME or len(nm) < 3:
            return None
        known = next((s for s in known_sets if s.lower() == nm.lower()), None)
        if not known:
            return None
        return f"{known} set"
    has_set = next((s for s in known_sets if s.lower() in nm.lower()), None)
    if not nm or (not has_set and len(nm.split()) == 1 and nm.lower() in ("pokémon", "pokemon", "mega")):
        if not fallback_set:
            return None
        nm = fallback_set
    if label == "Box":
        return f"{nm} Box"
    if label == "Collection" and nm.lower().endswith("collection"):
        return nm
    typed = m.group(0)
    if label in ("Collection", "Box", "Tin", "Battle Deck") and typed.lower() not in (label.lower(), label.lower() + "s"):
        typed = _re.sub(r"(es|s)$", "", typed) if typed.lower().endswith(("tins", "decks", "collections")) else typed
        return f"{nm} {typed.title().replace('Ex ', 'ex ')}"
    return f"{nm} {label}"


def first_set(text, known_sets):
    low = text.lower()
    hits = [(low.find(s.lower()), s) for s in known_sets if s.lower() in low]
    return min(hits)[1] if hits else None


def classify(window, product):
    """What happens on that date, and where."""
    w = window.lower()
    if _re.search(r"pre-?releases?", w):
        kind = "Prerelease events"
    elif _re.search(r"pre-?orders?", w):
        kind = "Pre-orders open"
    elif "restock" in w:
        kind = "Restock"
    else:
        kind = "Release"
    if "pokémon center" in product.lower() or "pokemon center" in product.lower():
        where = "Pokémon Center"
    elif _re.search(r"pok[eé]mon center", w) and (kind == "Pre-orders open" or
                                                  _re.search(r"early|first|exclusive|only", w)):
        where = "Pokémon Center"
    elif kind == "Prerelease events":
        where = "Local game stores"
    else:
        m = STORES.search(window)
        if m:
            k = m.group(1).lower()
            where = STORE_LABEL.get(k, m.group(1).title().replace("'S", "'s").replace("Gamestop", "GameStop"))
        else:
            where = "Stores & online"
    return kind, where


def split_sentences(text):
    t = _re.sub(r"\b(" + _MON + r")\.", r"\1", text, flags=_re.I)        # "Nov. 6" isn't a sentence end
    t = _re.sub(r"\b(approx|est|vs|no|inc)\.", r"\1", t, flags=_re.I)
    return [s.strip() for s in _re.split(r"(?<=[.!?])\s+(?=[A-Z\"“(])", t) if s.strip()]


def calendar_entry(seg, base):
    """A calendar line like '6 Nov 2026 · English · In 21 days · Delta Reign · English · Mega Rayquaza ex'."""
    if len(seg) > 260 or " · " not in seg:
        return None
    parts = [p.strip() for p in seg.split(" · ") if p.strip()]
    langs = {p.lower() for p in parts if p.lower() in ("english", "japanese", "chinese", "korean")}
    if not langs:
        return None
    if "english" not in langs:
        return "skip"
    dates = find_dates(seg, base)
    if not dates:
        return None
    for p in parts:
        if find_dates(p, base) or p.lower() in ("english", "japanese", "due", "out", "released", "upcoming") or \
                _re.match(r"^(in \d+ days?|today|tomorrow|date not confirmed|\d+ days? (?:ago|left))$", p, _re.I):
            continue
        if 3 <= len(p) <= 45 and p[0].isupper() and not p.lower().endswith(" ex"):
            return (dates[0], p, bool(HEDGE.search(seg)))
    return None


def events_from_doc(doc, known_sets, today):
    """Every dated drop in one post or page: [{product, date, exact, kind, where, status, ...}]"""
    base = (doc.get("pub") or eastern_now()).date()
    title = doc.get("title") or ""
    title_set = first_set(title, known_sets)
    segs = [("", title)] if title else []
    segs += html_segments(doc.get("html") or "")
    out = []
    for heading, seg in segs:
        ctx_set = first_set(heading, known_sets) or title_set
        cal = calendar_entry(seg, base)
        if cal == "skip":
            continue
        if cal:
            (a, b, when, exact, raw), name, hedged = cal
            prod = name if PTYPE_RX.search(name) and not _re.search(r"\b(set|expansion)\b", name, _re.I) else f"{name} set"
            out.append({"product": prod, "date": when, "exact": exact, "kind": "Release",
                        "where": "Stores & online", "hedged": hedged or not exact, "text": seg[:300]})
            continue
        for sent in split_sentences(seg):
            if len(sent) > 600 or (JAPAN.search(sent) and not ENGLISH.search(sent)):
                continue
            dates = find_dates(sent, base)
            if not dates:
                continue
            fallback = first_set(sent, known_sets) or ctx_set
            prods, prev_end = [], 0
            for m in PTYPE_RX.finditer(sent):
                if any(a <= m.start() < b for a, b, *_ in dates):
                    continue
                p = product_name(sent, m, known_sets, fallback, prev_end)
                prev_end = m.end()
                if p and not any(p.lower() == q[0].lower() for q in prods):
                    prods.append((p, m.start()))
            if not prods and fallback and RELEASE_WORD.search(sent):
                prods = [(f"{fallback} set", sent.lower().find(fallback.lower()))]
            if not prods:
                continue
            hedged_sent = bool(HEDGE.search(sent))
            for a, b, when, exact, raw in dates:
                window = sent[max(0, a - 90): b + 50]
                if SKIP_DATE.search(window):
                    continue
                if not (RELEASE_WORD.search(window) or _re.search(r"pre-?orders?|pre-?releases?|restock", window, _re.I)
                        or len(dates) == 1 and len(sent) < 140):
                    continue
                if len(dates) == 1:
                    mine = prods
                else:      # each product gets the date closest to it
                    mine = [p for p in prods if min(dates, key=lambda d: abs(d[0] - p[1]))[0] == a]
                for p, _ in mine:
                    kind, where = classify(window, p)
                    if kind == "Pre-orders open" and _re.search(r"(ship|releas|arriv|launch|hit|out|drop)\w*\s+(?:on\s+)?$",
                                                                sent[max(0, a - 25):a], _re.I):
                        kind = "Release"            # "available for pre-order now and ships November 6"
                    out.append({"product": p, "date": when, "exact": exact, "kind": kind, "where": where,
                                "hedged": hedged_sent or not exact, "text": sent[:300]})
    keep = []
    for e in out:
        lo, hi = today - timedelta(days=NEWS_DAYS_BACK), today + timedelta(days=NEWS_DAYS_AHEAD)
        end = e["date"] if e["exact"] else e["date"].replace(day=28)
        if lo <= end and e["date"] <= hi:
            keep.append(e)
    return keep


def norm_product(p):
    t = p.lower().replace("é", "e").replace("’", "'")
    t = _re.sub(r"\bpokemon (?:tcg|trading card game)\b|\bmega evolution\b|\bthe\b", " ", t)
    t = _re.sub(r"\belite trainer box(?:es)?\b|\betbs?\b", "etb", t)
    t = _re.sub(r"\bultra[- ]premium collections?\b|\bupcs?\b", "upc", t)
    t = _re.sub(r"\bsuper[- ]premium collections?\b|\bspcs?\b", "spc", t)
    t = _re.sub(r"\b(box|tin|bundle|blister|pack|deck|collection)(?:es|s)\b", r"\1", t)
    t = _re.sub(r"[^a-z0-9]+", " ", t)
    return " ".join(t.split())


def build_calendar(events, today):
    """Merge what every source said into one line per product + kind."""
    groups = {}
    for e in events:
        key = (norm_product(e["product"]), e["kind"], e["where"] if e["where"] == "Pokémon Center" else "")
        groups.setdefault(key, []).append(e)
    items = []
    for (_, kind, _), evs in groups.items():
        by_date = {}
        for e in evs:
            by_date.setdefault((e["date"], e["exact"]), []).append(e)

        def score(k):
            lst = by_date[k]
            srcs = {x["source"] for x in lst}
            firm = sum(1 for x in lst if not x["hedged"])
            official = any(x.get("official") for x in lst)
            return (official, k[1], len({x["source"] for x in lst if not x["hedged"]}), len(srcs), firm)

        best = max(by_date, key=score)
        lst = by_date[best]
        when, exact = best
        srcs = []
        for x in sorted(lst, key=lambda x: (not x.get("official"), x["hedged"])):
            if x["source"] not in srcs:
                srcs.append(x["source"])
        firm = [x for x in lst if not x["hedged"]]
        others = sorted({d for d, ex in by_date if (d, ex) != best and ex})
        status = "Confirmed" if exact and firm else "Predicted"
        main = (firm or lst)[0]
        name = max((x["product"] for x in lst), key=lambda n: (sum(c.isupper() for c in n), -len(n)))
        if exact:
            label = when.strftime("%a, %b ") + str(when.day)
        else:
            label = when.strftime("%b %Y")
        where = main["where"]
        cat = "pkc" if where == "Pokémon Center" else ("set" if name.endswith(" set") else
                                                        "store" if where not in ("Stores & online",) else "product")
        items.append({"product": name, "kind": kind, "where": where, "date": when.isoformat(), "exact": exact,
                      "label": label, "status": status, "sources": srcs, "link": main["link"],
                      "category": cat, "past": when < today,
                      "also": [d.strftime("%b ") + str(d.day) for d in others][:2],
                      "evidence": main["text"]})
    # A set line plus its own ETB/booster lines on the same day is fine (you want every product),
    # but drop a vague "Jan 2027" guess when an exact date for the same thing exists.
    exact_keys = {(norm_product(i["product"]), i["kind"]) for i in items if i["exact"]}
    items = [i for i in items if i["exact"] or (norm_product(i["product"]), i["kind"]) not in exact_keys]
    items.sort(key=lambda i: (i["date"], i["status"] != "Confirmed", i["product"]))
    return items


# ---- one source ----
def read_source(name, url, known_sets, article_cache, today):
    """All the drop dates one source mentions. Raises if the source can't be read at all."""
    raw = get_url(url, "news-" + _re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:20])
    official = bool(_re.search(r"(^|\.)pokemon\.com$", site_host(url)))
    items = feed_items(raw)
    docs = []
    if items is None:
        tm = _re.search(r"<title[^>]*>(.*?)</title>", raw[:20000], _re.S | _re.I)
        docs.append({"title": strip_tags(tm.group(1)) if tm else "", "link": url, "pub": None, "html": raw})
    else:
        reads = 0
        for title, link, pub, body in items[:40]:
            if pub and (datetime.now().astimezone() - pub).days > 120:
                continue
            doc = {"title": title, "link": link, "pub": pub, "html": body}
            topical = PTYPE_RX.search(title) or first_set(title, known_sets) or \
                _re.search(r"releas|pre-?order|arriv|launch|drop|restock|reveal|announc", title, _re.I)
            if link in article_cache:
                doc["html"] = article_cache[link]
            elif topical and len(strip_tags(body)) < 1500 and reads < MAX_ARTICLES:
                reads += 1           # the feed only has a teaser: read the whole post (once)
                try:
                    page = get_url(link, hidden_ok=False)
                    doc["html"] = page
                    article_cache[link] = page[:400000]
                except Exception:
                    pass
            docs.append(doc)
    for d in docs:
        for s in find_sets(strip_tags(d["title"] + " " + d["html"][:200000])):
            if s not in known_sets and len(known_sets) < 80:
                known_sets.append(s)
    events = []
    for d in docs:
        for e in events_from_doc(d, known_sets, today):
            e.update(source=name, link=d["link"], official=official)
            events.append(e)
    return events


def _json_default(o):
    return o.isoformat() if hasattr(o, "isoformat") else str(o)


def load_news_cache():
    try:
        with open(NEWS_CACHE) as f:
            c = json.load(f)
        for evs in c.get("events", {}).values():
            for e in evs:
                e["date"] = datetime.fromisoformat(e["date"]).date()
        return c
    except Exception:
        return {"events": {}, "articles": {}}


def refresh_news():
    """Read every source; a source that fails keeps the dates it gave last time."""
    if not news_busy.acquire(blocking=False):
        news_busy.acquire()
        news_busy.release()
        return load_news()
    try:
        feeds = parse_feeds_setting(load_settings()["news_feeds"])
        cache = load_news_cache()
        articles = cache.get("articles", {})
        today = eastern_now().date()
        known = list(BUILTIN_SETS)
        results, errors = {}, []

        def one(name, url):
            try:
                results[name] = read_source(name, url, known, articles, today)
            except Exception as e:
                errors.append(f"{name}: {why_failed(e)}")
                results[name] = cache.get("events", {}).get(name, [])

        # Calendars and sets first, so the news posts can recognize every set name.
        threads = [threading.Thread(target=one, args=f, daemon=True) for f in feeds]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=150)
        events = [e for lst in results.values() for e in lst]
        items = build_calendar(events, today)
        news = {"updated": datetime.now().astimezone().isoformat(), "items": items, "errors": sorted(errors),
                "sources": [n for n, _ in feeds]}
        os.makedirs(DATA_DIR, exist_ok=True)
        keep_links = list(articles)[-300:]
        with news_lock:
            with open(NEWS_CACHE, "w") as f:
                json.dump({"events": results, "articles": {k: articles[k] for k in keep_links}}, f,
                          default=_json_default)
            with open(NEWS_FILE, "w") as f:
                json.dump(news, f)
        log(f"Releases & drops refreshed ({len(items)} dates).")
        return news
    finally:
        news_busy.release()


def load_news():
    try:
        with news_lock, open(NEWS_FILE) as f:
            n = json.load(f)
        if n.get("items") and "product" not in n["items"][0]:
            raise ValueError("old format")
        return n
    except Exception:
        return {"updated": None, "items": [], "errors": [], "sources": []}


def news_loop():
    while True:
        try:
            refresh_news()
        except Exception as e:
            log(f"News refresh error: {e}")
        time.sleep(NEWS_EVERY)


# ---------------- invisible page reading (no windows, no tabs) ----------------
# The Target and Walmart checkers read pages with a hidden copy of Chrome that has no window
# and no Dock icon, so nothing pops up while you're using your Mac. It renders the page
# (runs its scripts) and hands back the finished page, which is read below.
from html.parser import HTMLParser
from urllib.parse import urljoin

CHROME_BIN = os.environ.get("QW_CHROME_BIN") or "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
_chrome_ua = {}

VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source",
             "track", "wbr"}
BLOCK_TAGS = {"address", "article", "aside", "blockquote", "br", "dd", "div", "dl", "dt", "fieldset",
              "figcaption", "figure", "footer", "form", "h1", "h2", "h3", "h4", "h5", "h6", "header", "hr",
              "li", "main", "nav", "ol", "p", "pre", "section", "table", "tr", "ul", "button", "label",
              "summary", "details", "td", "th"}
SKIP_TAGS = {"script", "style", "noscript", "template", "svg", "head", "iframe"}
PRODUCT_HREF = _re.compile(r"/-/A-(\d+)|/ip/(?:[^/]+/)?(\d+)")


class _Node:
    __slots__ = ("tag", "attrs", "kids", "parent")

    def __init__(self, tag, attrs, parent):
        self.tag, self.attrs, self.kids, self.parent = tag, attrs, [], parent


class _Tree(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = _Node("root", {}, None)
        self.cur = self.root

    def handle_starttag(self, tag, attrs):
        n = _Node(tag, {k: (v if v is not None else "") for k, v in attrs}, self.cur)
        self.cur.kids.append(n)
        if tag not in VOID_TAGS:
            self.cur = n

    def handle_startendtag(self, tag, attrs):
        self.cur.kids.append(_Node(tag, {k: (v if v is not None else "") for k, v in attrs}, self.cur))

    def handle_endtag(self, tag):
        x = self.cur
        while x is not self.root and x.tag != tag:
            x = x.parent
        if x is not self.root:
            self.cur = x.parent

    def handle_data(self, data):
        self.cur.kids.append(data)


def _iter(n):
    for k in n.kids:
        if isinstance(k, _Node):
            yield k
            yield from _iter(k)


def _hidden(n):
    st = (n.attrs.get("style") or "").replace(" ", "").lower()
    return "hidden" in n.attrs or "display:none" in st or "visibility:hidden" in st


def _text(n, cache):
    """Roughly what a browser shows as the element's text (innerText)."""
    key = id(n)
    if key in cache:
        return cache[key]
    out = []

    def walk(x):
        if isinstance(x, str):
            out.append(x)
            return
        if x.tag in SKIP_TAGS or _hidden(x):
            return
        blk = x.tag in BLOCK_TAGS
        if blk:
            out.append("\n")
        for k in x.kids:
            walk(k)
        if blk:
            out.append("\n")

    walk(n)
    lines = [_re.sub(r"[ \t\r\f\v ]+", " ", l).strip() for l in "".join(out).split("\n")]
    t = "\n".join(l for l in lines if l)
    cache[key] = t
    return t


def _pid(href):
    m = PRODUCT_HREF.search(href or "")
    return (m.group(1) or m.group(2)) if m else None


def extract_page(html, url):
    """Turn a finished page into the same info the in-Chrome reader collects."""
    t = _Tree()
    try:
        t.feed(html)
    except Exception:
        pass
    root, cache = t.root, {}
    nodes = list(_iter(root))
    title = next(("".join(k for k in n.kids if isinstance(k, str)).strip() for n in nodes if n.tag == "title"), "")
    ld = []
    for n in nodes:
        if n.tag == "script" and "ld+json" in (n.attrs.get("type") or ""):
            try:
                ld.append(json.loads("".join(k for k in n.kids if isinstance(k, str))))
            except Exception:
                pass
    buttons = []
    for n in nodes:
        if n.tag == "button" or n.attrs.get("role") == "button" or (n.tag == "input" and n.attrs.get("type") == "submit"):
            if _hidden(n):
                continue
            txt = (_text(n, cache) or n.attrs.get("value") or n.attrs.get("aria-label") or "").strip()
            if txt and len(txt) < 50:
                buttons.append({"t": txt.split("\n")[0], "d": "disabled" in n.attrs or n.attrs.get("aria-disabled") == "true"})
            if len(buttons) >= 80:
                break
    anchors = [n for n in nodes if n.tag == "a" and _pid(n.attrs.get("href"))][:300]
    cards, seen = [], set()
    for a in anchors:
        pid = _pid(a.attrs.get("href"))
        if pid in seen:
            continue
        el, steps = a, 0
        while el is not None and el is not root and steps < 10 and not _re.search(r"\$\s?\d", _text(el, cache)):
            el, steps = el.parent, steps + 1
        if el is None or el is root:
            continue
        while el.parent is not None and el.parent is not root:
            p = el.parent
            ids = {_pid(x.attrs.get("href")) for x in _iter(p) if x.tag == "a" and _pid(x.attrs.get("href"))}
            if len(ids) > 1 or len(_text(p, cache)) > 1500:
                break
            el = p
        name = (_text(a, cache) or a.attrs.get("aria-label") or a.attrs.get("title") or "").strip().split("\n")[0]
        if len(name) < 6:
            h = next((x for x in _iter(el) if x.tag in ("h2", "h3") or "title" in (x.attrs.get("data-test") or "")
                      or "title" in (x.attrs.get("class") or "")), None)
            if h is not None:
                name = _text(h, cache).strip().split("\n")[0]
        if len(name) < 6:
            continue
        seen.add(pid)
        cards.append({"href": urljoin(url, a.attrs.get("href")).split("#")[0], "name": name[:200],
                      "text": _text(el, cache)[:900]})
        if len(cards) >= 60:
            break
    body = next((n for n in nodes if n.tag == "body"), root)
    return {"url": url, "title": title, "text": _text(body, cache)[:15000], "ld": ld, "buttons": buttons,
            "cards": cards}


def headless_available():
    return os.path.exists(CHROME_BIN)


def chrome_user_agent():
    """Look like normal Chrome (headless Chrome otherwise says 'HeadlessChrome')."""
    if "ua" not in _chrome_ua:
        major = "141"
        try:
            v = subprocess.run([CHROME_BIN, "--version"], capture_output=True, text=True, timeout=20).stdout
            m = _re.search(r"(\d+)\.\d+\.\d+\.\d+", v)
            if m:
                major = m.group(1)
        except Exception:
            pass
        _chrome_ua["ua"] = (f"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                            f"(KHTML, like Gecko) Chrome/{major}.0.0.0 Safari/537.36")
    return _chrome_ua["ua"]


def dump_dom(name, url):
    """The finished page from a hidden copy of Chrome (no window, no Dock icon)."""
    profile = os.path.join(DATA_DIR, f"checker-chrome-{name}")      # one per checker, so they can run at once
    os.makedirs(profile, exist_ok=True)
    cmd = [CHROME_BIN, "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
           "--hide-scrollbars", "--mute-audio", "--disable-extensions", f"--user-data-dir={profile}",
           "--window-size=1280,2400", f"--user-agent={chrome_user_agent()}", "--lang=en-US",
           "--virtual-time-budget=15000", "--dump-dom", url]
    if os.environ.get("QW_HEADLESS_SANDBOX_OFF"):     # only for the test machine (runs as root)
        cmd.insert(1, "--no-sandbox")
    # Its own process group, so every helper process it starts is closed afterwards,
    # even if a page hangs. Nothing is left running in the background.
    import signal
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                            start_new_session=True)
    try:
        out, _ = proc.communicate(timeout=90)
    except subprocess.TimeoutExpired:
        out = None
    finally:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except Exception:
            pass
        try:
            proc.wait(timeout=5)
        except Exception:
            pass
    if out is None:
        raise RuntimeError("the page took too long to load. It'll try again at the next check")
    return out


def read_headless(name, url):
    out = dump_dom(name, url)
    if "<" not in (out or ""):
        raise RuntimeError("couldn't load the page this time. It'll try again at the next check")
    d = extract_page(out, url)
    if "SWWRGTS" in out:                      # Pokémon Center's waiting-room script
        d["text"] += " " + SCRIPT_MARK
    return d


# ---------------- Target restocks & Walmart drawings ----------------
# Two extra checkers that run on their own, at the same time as the Pokémon Center watcher.
# Each uses its own minimized Chrome window, reads the page, and alerts you. Neither one buys anything.
TARGET_SEARCH = "https://www.target.com/s?searchTerm=pokemon+trading+cards"
WALMART_DRAW = "https://www.walmart.com/shop/collectibles/draw"
BESTBUY_SEARCH = "https://www.bestbuy.com/site/searchpage.jsp?st=pokemon+trading+card+game"
# Walmart search filtered to items sold by Walmart itself (not Marketplace sellers).
WALMART_SEARCH = "https://www.walmart.com/search?q=pokemon+trading+card+game&facet=retailer_type%3AWalmart"
SITE_NAMES = {"target": "Target", "walmart": "Walmart", "bestbuy": "Best Buy"}
WALMART_SEEN_FILE = os.path.join(DATA_DIR, "walmart_seen.json")
CHECK_GAP = (5, 10)             # seconds between page loads inside one check (gentle on the site)
BLOCK_PAUSE = 30 * 60           # if a site shows a robot check, back off this long
CHECKER_BLOCK = _re.compile(r"robot or human|press (?:&|and) hold|verify (?:that )?(?:you are|you're) (?:a )?human|"
                            r"enter the characters you see|make sure you'?re not a robot|to discuss automated access|"
                            r"click the button below to continue shopping|"
                            r"access denied|unusual (?:activity|traffic)|are you a robot|pardon our interruption|"
                            r"request unsuccessful", _re.I)
POKEMON = _re.compile(r"pok[eé]mon", _re.I)
# Anything that's a chance-to-buy: drawings, raffles, lotteries, invites, sweepstakes, entry windows.
CHANCE = r"(?:drawings?|draws?|raffles?|lotter(?:y|ies)|invites?|invitations?|sweepstakes?|sweeps|entr(?:y|ies))"
CHANCE_OPEN = _re.compile(
    r"(?:enter|join|request|apply|sign up|register|submit)(?:\s+(?:for|to|in(?:to)?))?\s+(?:the\s+|an?\s+|your\s+)?" + CHANCE +
    r"|" + CHANCE + r"\s+(?:is\s+)?(?:now\s+)?(?:open|live|active)\b|" + CHANCE + r"\s+(?:ends|closes)\b"
    r"|invite[- ]only|invitation to (?:purchase|buy)|invite request|request an? invite"
    r"|enter (?:for )?(?:a|the|your) chance|chance to (?:buy|purchase)|enter now", _re.I)
CHANCE_SOON = _re.compile(
    CHANCE + r"\s+(?:window\s+)?(?:opens|starts|begins|coming soon|opens soon)\b|(?:upcoming|next)\s+" + CHANCE +
    r"|" + CHANCE + r"\s+(?:window|period)\b", _re.I)
CHANCE_DONE = _re.compile(
    CHANCE + r"\s+(?:has\s+)?(?:ended|closed|is closed|is over)\b|(?:invites?|entr(?:y|ies)|request)\s+(?:sent|submitted|received|requested)"
    r"|you(?:'re| are| have been)\s+(?:on the list|entered|registered)|you(?:'ve| have) (?:entered|requested)"
    r"|not selected|winners? (?:have been )?(?:selected|notified|chosen)", _re.I)


def chance_state(text, buttons=()):
    """'open' / 'soon' / None for a drawing, raffle, invite, lottery... on a store page."""
    t = text[:4000]
    live_btn = any(CHANCE_OPEN.search(b.get("t", "")) and not b.get("d") for b in buttons)
    if live_btn or (CHANCE_OPEN.search(t) and not CHANCE_DONE.search(t)):
        return "open"
    if CHANCE_SOON.search(t) and not CHANCE_DONE.search(t):
        return "soon"
    return None
TCG_WORDS = _re.compile(r"trading card|\btcg\b|booster|elite trainer|\betb\b|collection|\btins?\b|blister|\bcards?\b",
                        _re.I)
PRICE_RE = _re.compile(r"\$\s?(\d{1,4}(?:,\d{3})*(?:\.\d{2})?)")
BUY_BTN = _re.compile(r"add to cart|ship it|deliver it|pick it up|buy now|pre-?order", _re.I)
OUT_OF_STOCK = _re.compile(r"out of stock|sold out|notify me when (?:it'?s|it’s) back|temporarily unavailable|"
                          r"currently unavailable|this item isn'?t available|not available (?:online|for shipping)|"
                          r"no longer available|coming soon", _re.I)
OTHER_PRODUCTS = _re.compile(r"\n(?:similar items|guests also viewed|guests also bought|frequently bought together|"
                             r"more to consider|you may also like|related items|recommended for you|"
                             r"more from this brand|featured items|explore more)\b", _re.I)
DRAW_RX = _re.compile(r"(?:drawing|raffle|lottery|entry|entries|invites?|sweepstakes)\s+"
                     r"(ended|ends|closes|closed|opens|starts|begins|is open|now open|is live|open|live)\b[:\s]*([^\n]*)", _re.I)

# Official retail prices (Target / Pokémon). You're alerted up to "allowance" dollars over these.
# Most specific first: the first line whose name is in the product title wins.
MSRP_DEFAULT = "\n".join([
    # Target's own shelf prices (checked on target.com, Oct 2026), most specific first.
    "30th Celebration Ultra Premium Collection | 249.99", "30th Celebration Elite Trainer Box | 69.99",
    "Ultra Premium Collection | 179.99", "Super Premium Collection | 129.99",
    "Pokemon Center Elite Trainer Box | 59.99", "Elite Trainer Box | 59.99",
    "Booster Bundle | 31.99", "Booster Box | 179.99",
    "Black Kyurem ex & Melmetal ex | 49.99", "Binder Collection | 39.99", "Figure Collection | 39.99",
    "Illustration Collection | 17.99", "Poster Collection | 19.99", "Sticker Collection | 19.99",
    "Knock Out Collection | 11.99", "Premium Collection | 49.99", "Battle Deck | 24.99",
    "Collector Chest | 34.99", "Mini Tin Display | 129.90", "Mini Tin | 12.99", "Tin | 29.99",
    "ex Box | 29.99", "2 Pack Blister | 12.99", "Checklane Blister | 6.99", "Blister | 14.99",
    "Sleeved Booster | 5.99", "Booster Pack | 5.99", "Collection | 29.99", "Box | 29.99"])
# Official MSRPs (Pokémon's suggested prices, Oct 2026) for Best Buy and Walmart, which mostly sell
# at MSRP. Most specific first. Each store has its own list you can edit (Stores tab → Price limits).
MSRP_GENERAL = "\n".join([
    "30th Celebration Ultra Premium Collection | 179.99", "Ultra Premium Collection | 179.99",
    "Super Premium Collection | 89.99", "Pokemon Center Elite Trainer Box | 59.99", "Elite Trainer Box | 49.99",
    "Booster Bundle | 26.94", "Booster Box | 161.64", "Premium Figure Collection | 69.99",
    "Premium Poster Collection | 39.99", "Binder Collection | 31.99", "Figure Collection | 29.99",
    "Special Collection | 29.99", "Illustration Collection | 17.99", "Poster Collection | 14.99",
    "Sticker Collection | 14.99", "Knock Out Collection | 9.99", "Premium Collection | 39.99",
    "Build & Battle | 29.99", "Battle Deck | 19.99", "Collector Chest | 29.99", "Mini Tin Display | 99.90",
    "Mini Tin | 9.99", "ex Box | 21.99", "Tin | 24.99", "3 Pack Blister | 14.99", "2 Pack Blister | 9.99",
    "Checklane Blister | 5.99", "Blister | 14.99", "Sleeved Booster | 4.99", "Booster Pack | 4.99",
    "Collection | 29.99", "Box | 29.99"])
MSRP_STORES = ("bestbuy", "walmart")
ROUNDING = 0.49      # a small cushion on every limit, so a few cents of rounding never blocks an alert
# Earlier defaults, upgraded automatically if you never edited them.
OLD_MSRP_DEFAULTS = ["\n".join([
    "30th Celebration Ultra Premium Collection | 249.99",
    "Pokemon Center Elite Trainer Box | 69.99",
    "Ultra Premium Collection | 119.99", "Super Premium Collection | 149.99",
    "Elite Trainer Box | 69.99", "Booster Bundle | 26.94", "Booster Box | 161.64",
    "Binder Collection | 31.99", "Figure Collection | 29.99", "Illustration Collection | 19.99",
    "Poster Collection | 14.99", "Sticker Collection | 14.99", "Knock Out Collection | 9.99",
    "Premium Collection | 39.99", "Battle Deck | 19.99", "Collector Chest | 29.99",
    "Mini Tin Display | 99.90", "Mini Tin | 9.99", "Tin | 21.99", "ex Box | 21.99",
    "2 Pack Blister | 9.99", "Checklane Blister | 5.99", "Blister | 13.99",
    "Sleeved Booster | 4.99", "Booster Pack | 4.99", "Collection | 21.99", "Box | 29.99"]), "\n".join([
    "30th Celebration Ultra Premium Collection | 179.99",
    "Pokemon Center Elite Trainer Box | 59.99",
    "Ultra Premium Collection | 119.99", "Super Premium Collection | 149.99",
    "Elite Trainer Box | 49.99", "Booster Bundle | 26.94", "Booster Box | 161.64",
    "Binder Collection | 31.99", "Figure Collection | 29.99", "Illustration Collection | 19.99",
    "Poster Collection | 14.99", "Sticker Collection | 14.99", "Knock Out Collection | 9.99",
    "Premium Collection | 39.99", "Battle Deck | 19.99", "Collector Chest | 29.99",
    "Mini Tin Display | 99.90", "Mini Tin | 9.99", "Tin | 21.99", "ex Box | 21.99",
    "2 Pack Blister | 9.99", "Checklane Blister | 5.99", "Blister | 13.99",
    "Sleeved Booster | 4.99", "Booster Pack | 4.99", "Collection | 21.99", "Box | 29.99"]), "\n".join([
    "Elite Trainer Box | 59.99", "Booster Bundle | 29.99", "Booster Box | 165.00",
    "Ultra Premium Collection | 129.99", "Super Premium Collection | 99.99", "Premium Collection | 49.99",
    "Collector Chest | 29.99", "Mini Tin | 9.99", "Tin | 24.99",
    "Checklane Blister | 6.99", "Blister | 14.99",
    "Sleeved Booster | 4.99", "Booster Pack | 4.99", "Binder Collection | 39.99",
    "Collection | 29.99", "Box | 39.99"]), "\n".join([
    "Elite Trainer Box | 59.99", "Booster Bundle | 29.99", "Booster Box | 165.00",
    "Ultra Premium Collection | 129.99", "Super Premium Collection | 99.99", "Premium Collection | 49.99",
    "Collector Chest | 29.99", "Mini Tin | 9.99", "Tin | 24.99", "Blister | 19.99",
    "Sleeved Booster | 4.99", "Booster Pack | 4.99", "Binder Collection | 39.99",
    "Collection | 29.99", "Box | 39.99", "Anything else | 59.99"])]
# Never real retail at MSRP: imports, repacks, mystery boxes, graded cards, lots.
NOT_RETAIL = _re.compile(r"\b(japanese|japan|korean|chinese|thai|import(?:ed)?|repack(?:ed)?|value pack|mystery|"
                         r"lot of|\d+\s*card lot|graded|psa|cgc|bgs|slab|custom|random (?:card|pack) lot|bulk)\b", _re.I)

# One line of JavaScript that reads a store page: text, prices in page data, buttons, product cards.
READ_JS = (
    "(function(){"
    "var o={url:location.href,title:document.title,text:(document.body?document.body.innerText:'').slice(0,15000),"
    "ld:[],buttons:[],cards:[]};"
    "var ss=document.querySelectorAll('script[type=\"application/ld+json\"]');"
    "for(var i=0;i<ss.length;i++){try{o.ld.push(JSON.parse(ss[i].textContent))}catch(e){}}"
    "var bs=document.querySelectorAll('button,[role=button],input[type=submit]');"
    "for(var j=0;j<bs.length&&o.buttons.length<80;j++){var b=bs[j];var t=(b.innerText||b.value||'').trim();"
    "if(t&&t.length<50)o.buttons.push({t:t,d:!!(b.disabled||b.getAttribute('aria-disabled')==='true')});}"
    "var sel='a[href*=\"/-/A-\"],a[href*=\"/ip/\"],a[href*=\"skuId=\"],a[href*=\"/product/\"]';"
    "var key=function(h){var m=h.match(/\\/-\\/A-(\\d+)|\\/ip\\/(?:[^\\/]+\\/)?(\\d+)|skuId=(\\d+)/);return m?(m[1]||m[2]||m[3]):h.split('?')[0];};"
    "var seen={},as=document.querySelectorAll(sel);"
    "for(var k=0;k<as.length&&o.cards.length<60;k++){var a=as[k];var id=key(a.href);if(seen[id])continue;"
    "var el=a,n=0;while(el&&n<10&&!/\\$\\s?\\d/.test(el.innerText||'')){el=el.parentElement;n++;}"
    "if(!el)continue;"
    "while(el.parentElement){var p=el.parentElement;var ids={},ls=p.querySelectorAll(sel);"
    "for(var q=0;q<ls.length;q++)ids[key(ls[q].href)]=1;"
    "if(Object.keys(ids).length>1||(p.innerText||'').length>1500)break;el=p;}"
    "var name=(a.innerText||a.getAttribute('aria-label')||a.title||'').trim().split('\\n')[0];"
    "if(name.length<6){var h=el.querySelector('h2,h3,[data-test*=title],[class*=title]');"
    "if(h)name=(h.innerText||'').trim().split('\\n')[0];}"
    "if(name.length<6)continue;"
    "seen[id]=1;o.cards.push({href:a.href.split('#')[0],name:name.slice(0,200),text:(el.innerText||'').trim().slice(0,900)});}"
    "return JSON.stringify(o);})()"
)

checker_stop = threading.Event()
checkers = {n: {"status": "off", "message": "Off", "last": None, "next": None, "items": [], "tab": None,
                "paused_until": 0, "wake": threading.Event(), "alerted": set(), "block_pinged": False,
                "lock": threading.Lock(), "hidden_fails": 0, "tab_until": 0, "how": "",
                "last_result": "", "last_done": None, "took": None}
            for n in ("target", "walmart", "bestbuy")}


def first_price(text):
    m = PRICE_RE.search(text or "")
    return float(m.group(1).replace(",", "")) if m else None


def store_price(text):
    """Read a store price. Walmart shows cents in small raised text, so the page text can say
    "$69" + "00" run together ("$6900"), or "current price $69.00" for screen readers."""
    t = text or ""
    num = lambda x: float(x.replace(",", ""))
    m = _re.search(r"(?:current price|now)\s*:?\s*\$\s?(\d[\d,]*\.\d{2})", t, _re.I)
    if m:
        return num(m.group(1))
    m = _re.search(r"\$\s?(\d[\d,]*\.\d{2})", t)
    if m:
        return num(m.group(1))
    m = _re.search(r"\$\s?(\d{1,4})\s*\n\s*(\d{2})\b", t)            # "$69" then "00" on the next line
    if m:
        return num(m.group(1) + "." + m.group(2))
    m = _re.search(r"\$\s?(\d{3,6})\b", t)                                # "$6900" = $69.00
    if m:
        d = m.group(1)
        return num(d[:-2] + "." + d[-2:])
    return None


def parse_msrp(text):
    out = []
    for line in str(text).splitlines():
        if "|" not in line:
            continue
        name, _, val = line.rpartition("|")
        try:
            out.append((_re.sub(r"[\-\u2010-\u2015/]+", " ", normalize(name.strip())),
                        float(val.strip().lstrip("$").replace(",", ""))))
        except ValueError:
            pass
    return out


ALIASES = [(r"\betb\b", "elite trainer box"), (r"booster display", "booster box"),
           (r"\bupc\b", "ultra premium collection"), (r"\bspc\b", "super premium collection")]


def bundle_count(name):
    """How many of the item are in the listing. "3-Pack Blister" is one item, but
    "Booster Bundle 2-Pack", "Knock Out Collection 4-Count Bundle" and "... 2-Pack Bundle" are several."""
    m = _re.search(r"(\d+)\s*-?\s*(?:pack|pk|count|ct)\b(?=\s*(?:bundle\b|$|[,()\[\]|-]))|bundle of (\d+)|"
                   r"set of (\d+)|\b(\d+)\s*x\s|\bx\s*(\d+)\s*$|\((\d+)\s*(?:pack|pk|count|ct)?\)\s*$", name, _re.I)
    if m:
        n = int(next(g for g in m.groups() if g))
        return n if 1 < n <= 12 else 1
    # "Sylveon ex & Greninja ex Box Bundle" = two different ex boxes sold together
    if _re.search(r"\bex\b.*(?:&|\band\b).*\bex\s+box(?:es)?\s+bundle|\bex\s+box(?:es)?\s+bundle\b.*\bex\b.*(?:&|\band\b).*\bex\b",
                  name, _re.I):
        return 2
    return 1


def msrp_table(s, store="target"):
    if store in MSRP_STORES:
        return s.get(f"msrp_{store}") or MSRP_GENERAL
    return s["msrp_limits"]


def msrp_limit(name, s, store="target"):
    """That store's max price for this product (per-item limit x how many are in the bundle)."""
    low = _re.sub(r"[\-\u2010-\u2015/]+", " ", normalize(name))      # "Ultra-Premium" = "Ultra Premium"
    for rx, rep_ in ALIASES:
        low = _re.sub(rx, rep_, low)
    fallback = None
    for key, val in parse_msrp(msrp_table(s, store)):
        if key in ("anything else", "everything else", "other"):
            fallback = val
            continue
        if key and key in low:
            return round(val * bundle_count(name), 2)
    return round(fallback * bundle_count(name), 2) if fallback else None


def is_tcg(name):
    return bool(POKEMON.search(name) and TCG_WORDS.search(name))


def walk_ld(obj):
    if isinstance(obj, list):
        for x in obj:
            yield from walk_ld(x)
    elif isinstance(obj, dict):
        yield obj
        for v in obj.values():
            if isinstance(v, (list, dict)):
                yield from walk_ld(v)


def allowance_for(limit, s):
    """Up to your allowance over retail, but never more than 25% over (so a $6.99 blister
    can't alert at $16, while a $249.99 UPC still gets the full $10)."""
    return min(s["msrp_allowance"], round(limit * 0.25, 2))


def judge(item, s, store="target"):
    item["limit"] = msrp_limit(item["name"], s, store)
    if not item.get("in_stock"):
        item["status"] = "out"
    elif item.get("third_party"):
        item["status"] = "third"
    elif NOT_RETAIL.search(item["name"]):
        item["status"] = "notretail"
    elif item["limit"] is None:
        item["status"] = "nolimit"
    elif item.get("price") is not None and item["price"] > item["limit"] + allowance_for(item["limit"], s) + ROUNDING:
        item["status"] = "over"
    else:
        item["status"] = "in"
    return item


def parse_target_product(d, url, s):
    text = d.get("text", "")
    name = price = None
    ld_stock = None
    for o in walk_ld(d.get("ld", [])):
        if str(o.get("@type", "")).lower() == "product" and not name:
            name = o.get("name")
        offers = o.get("offers")
        for off in walk_ld(offers or []):
            if price is None and off.get("price") not in (None, ""):
                try:
                    price = float(str(off["price"]).replace(",", ""))
                except ValueError:
                    pass
            av = str(off.get("availability", "")).lower()
            if av:
                ld_stock = "instock" in av or "preorder" in av
    if not name:
        name = _re.sub(r"\s*:\s*Target\s*$", "", d.get("title", "")).strip() or url
    if price is None:
        pos = text.find(name[:25]) if name else -1
        price = first_price(text[pos:] if pos >= 0 else text)
    # Only look at the main product area. Target lists other products further down
    # ("Similar items", "Guests also viewed"...), each with its OWN "Add to cart" button.
    main = OTHER_PRODUCTS.split(text, 1)[0]
    buttons = d.get("buttons", [])
    buy = any(not b.get("d") and BUY_BTN.search(b.get("t", "")) for b in buttons)
    if OUT_OF_STOCK.search(main) or ld_stock is False:
        in_stock = False                       # the page itself says it's out: believe it
    elif ld_stock is True:
        in_stock = True
    else:
        in_stock = buy and bool(BUY_BTN.search(main))   # a buy button for THIS product
    seller = _re.search(r"sold (?:&|and) shipped by\s+([^\n]+)", main, _re.I)
    third = bool(seller and "target" not in seller.group(1).lower())
    return judge({"name": name[:160], "url": url, "price": price, "in_stock": in_stock, "third_party": third}, s)


BB_SOLD_OUT = _re.compile(r"\bsold out\b|coming soon|unavailable nearby|no longer available|not available|"
                          r"get notified|notify me", _re.I)
BB_BUY = _re.compile(r"add to cart|buy now|please wait|pre-?order", _re.I)
BB_OTHER = _re.compile(r"\n(?:customers also (?:viewed|bought)|similar products|compare similar products|"
                      r"frequently bought together|people also (?:viewed|bought)|recently viewed|"
                      r"customers who viewed this)\b", _re.I)
BB_SELLER = _re.compile(r"sold (?:(?:&|and) shipped )?by\s+([^\n]+)", _re.I)
# Best Buy's invite system: you request an invite (a drawing), and the people picked get 24 hours to buy.
BB_INVITE = CHANCE_OPEN
BB_INVITE_DONE = CHANCE_DONE
BESTBUY_SEEN_FILE = os.path.join(DATA_DIR, "bestbuy_invites_seen.json")


def parse_bestbuy_product(d, url, s):
    """A Best Buy product page: official Pokémon cards sold by Best Buy, in stock, at MSRP."""
    text = d.get("text", "")
    name = price = None
    ld_stock = None
    for o in walk_ld(d.get("ld", [])):
        if str(o.get("@type", "")).lower() == "product" and not name:
            name = o.get("name")
        for off in walk_ld(o.get("offers") or []):
            if price is None and off.get("price") not in (None, ""):
                try:
                    price = float(str(off["price"]).replace(",", ""))
                except ValueError:
                    pass
            av = str(off.get("availability", "")).lower()
            if av:
                ld_stock = "instock" in av or "preorder" in av
    if not name:
        name = _re.sub(r"\s*-\s*Best Buy\s*$", "", d.get("title", "")).strip() or url
    # Only the main product area counts: Best Buy lists other products further down
    # ("Customers also viewed"...), each with its OWN "Add to Cart" button.
    main = BB_OTHER.split(OTHER_PRODUCTS.split(text, 1)[0], 1)[0]
    if price is None:
        pos = main.find(name[:25]) if name else -1
        price = store_price(main[pos:] if pos >= 0 else main)
    buttons = d.get("buttons", [])
    buy = any(not b.get("d") and BB_BUY.search(b.get("t", "")) for b in buttons)
    if BB_SOLD_OUT.search(main) or ld_stock is False:
        in_stock = False                        # the page itself says it's out: believe it
    elif buy and BB_BUY.search(main):
        in_stock = True                         # "Add to Cart" (or the drop queue's "Please Wait") for THIS product
    else:
        in_stock = bool(ld_stock)
    seller = BB_SELLER.search(main)
    third = bool(seller and "best buy" not in seller.group(1).lower())
    invite = any(BB_INVITE.search(b.get("t", "")) and not b.get("d") for b in buttons) or \
        bool(BB_INVITE.search(main[:3000]) and not BB_INVITE_DONE.search(main[:3000]) and
             not any(BB_INVITE.search(b.get("t", "")) and b.get("d") for b in buttons))
    it = judge({"name": name[:160], "url": url, "price": price, "in_stock": in_stock or invite, "third_party": third}, s, "bestbuy")
    if invite and it["status"] in ("in", "nolimit", "over"):
        it["status"] = "invite"                 # invite requests are open: enter for a chance to buy
    return it


WM_OUT = _re.compile(r"\bout of stock\b|get in-stock alert|currently unavailable|not available|"
                     r"this item is no longer available", _re.I)
WM_SELLER = _re.compile(r"sold (?:(?:&|and) shipped )?by\s*\n?\s*([^\n]+)", _re.I)
WM_OTHER = _re.compile(r"\n(?:similar items you might like|customers also (?:considered|viewed|bought)|"
                     r"more items to explore|frequently bought together|popular items|sponsored products|"
                     r"compare similar items)\b", _re.I)


def parse_walmart_product(d, url, s):
    """A Walmart product page: official Pokémon cards SOLD BY WALMART (not Marketplace sellers), at MSRP."""
    text = d.get("text", "")
    name = None
    for o in walk_ld(d.get("ld", [])):
        if str(o.get("@type", "")).lower() == "product" and not name:
            name = o.get("name")
    if not name:
        name = _re.sub(r"\s*-\s*Walmart\.com\s*$", "", d.get("title", "")).strip() or url
    main = WM_OTHER.split(OTHER_PRODUCTS.split(text, 1)[0], 1)[0]
    pos = main.find(name[:25]) if name else -1
    price = store_price(main[pos:] if pos >= 0 else main)
    buttons = d.get("buttons", [])
    buy = any(not b.get("d") and _re.search(r"add to cart|pre-?order", b.get("t", ""), _re.I) for b in buttons)
    if WM_OUT.search(main[:4000]):
        in_stock = False
    else:
        in_stock = buy and bool(_re.search(r"add to cart|pre-?order", main, _re.I))
    seller = WM_SELLER.search(main)
    from_walmart = bool(seller and "walmart" in seller.group(1).lower())
    return judge({"name": name[:160], "url": url, "price": price, "in_stock": in_stock,
                  "third_party": not from_walmart}, s, "walmart")


def parse_walmart_search(d, s):
    """Search is already filtered to 'sold by Walmart'; each hit is double-checked on its own page."""
    items = []
    for c in d.get("cards", []):
        if not is_tcg(c["name"]) or "/ip/" not in c["href"]:
            continue
        t = c.get("text", "")
        if _re.search(r"\bsponsored\b", t, _re.I):
            continue
        in_stock = bool(_re.search(r"add to cart|pre-?order", t, _re.I)) and not WM_OUT.search(t)
        it = judge({"name": c["name"][:160], "url": c["href"].split("?")[0], "price": store_price(t),
                    "in_stock": in_stock, "third_party": False, "from_scan": True}, s, "walmart")
        if it["status"] == "in":
            items.append(it)
    return items


def parse_bestbuy_search(d, s):
    items = []
    for c in d.get("cards", []):
        if not is_tcg(c["name"]):
            continue
        t = c.get("text", "")
        in_stock = (bool(BB_BUY.search(t)) and not BB_SOLD_OUT.search(t)) or bool(BB_INVITE.search(t))
        seller = BB_SELLER.search(t)
        third = bool(seller and "best buy" not in seller.group(1).lower())
        it = judge({"name": c["name"][:160], "url": c["href"], "price": store_price(t), "in_stock": in_stock,
                    "third_party": third, "from_scan": True}, s, "bestbuy")
        if it["status"] == "in":
            items.append(it)
    return items


def parse_target_search(d, s):
    items = []
    for c in d.get("cards", []):
        if not is_tcg(c["name"]):
            continue
        t = c.get("text", "")
        in_stock = bool(_re.search(r"add to cart|pre-?order", t, _re.I)) and not \
            _re.search(r"out of stock|sold out|unavailable", t, _re.I)
        third = bool(_re.search(r"sold (?:&|and) shipped by\s+(?!target)|sold by\s+(?!target)", t, _re.I))
        it = judge({"name": c["name"][:160], "url": c["href"], "price": store_price(t), "in_stock": in_stock,
                    "third_party": third, "from_scan": True}, s)
        if it["status"] == "in":
            items.append(it)
    return items


# Walmart now shows each drawing as "Drawing entry window  Oct 21, 2:00pm - 3:00pm PDT".
WINDOW_RX = _re.compile(r"(?:(?:drawing|raffle|lottery|entry|entries|invite)\s+(?:entry\s+)?(?:window|period)|enter between)\W*([A-Za-z]{3,9})\.?\s+(\d{1,2}),?\s*(?:(\d{4})\s*,?\s*)?"
                        r"(\d{1,2})(?::(\d{2}))?\s*([ap])\.?m\.?\s*(?:-|–|—|to)\s*"
                        r"(?:([A-Za-z]{3,9})\.?\s+(\d{1,2}),?\s*)?(\d{1,2})(?::(\d{2}))?\s*([ap])\.?m\.?\s*([A-Z]{2,4})?", _re.I)
TZ_OFFSETS = {"ET": -4, "EDT": -4, "EST": -5, "CT": -5, "CDT": -5, "CST": -6, "MT": -6, "MDT": -6, "MST": -7,
              "PT": -7, "PDT": -7, "PST": -8}


def walmart_window(t):
    """(status, when-text) from Walmart's "Drawing entry window ..." line, or None if it isn't there."""
    m = WINDOW_RX.search(t)
    if not m:
        return None
    from datetime import timezone, timedelta as _td
    mon1, day1, yr, h1, mi1, ap1, mon2, day2, h2, mi2, ap2, tz = m.groups()
    tzname = (tz or "ET").upper()
    off = TZ_OFFSETS.get(tzname, -4)
    if tzname == "PT":
        off = -7 if 3 <= datetime.utcnow().month <= 10 else -8
    zone = timezone(_td(hours=off))
    now = datetime.now(timezone.utc)

    def mk(mon, day, h, mi, ap):
        try:
            mo = datetime.strptime(mon[:3].title(), "%b").month
        except ValueError:
            return None
        hh = int(h) % 12 + (12 if ap.lower() == "p" else 0)
        year = int(yr) if yr else now.year
        dt = datetime(year, mo, int(day), hh, int(mi or 0), tzinfo=zone)
        if not yr and (dt - now).days > 200:      # "Jan 3" seen in late December
            dt = dt.replace(year=year - 1)
        elif not yr and (now - dt).days > 200:
            dt = dt.replace(year=year + 1)
        return dt

    start = mk(mon1, day1, h1, mi1, ap1)
    end = mk(mon2 or mon1, day2 or day1, h2, mi2, ap2)
    if not start or not end:
        return None
    et = timezone(_td(hours=-4 if 3 <= now.month <= 10 else -5))
    fmt = lambda x: x.astimezone(et).strftime("%b %d, %I:%M %p").replace(" 0", " ")
    if now < start:
        return "upcoming", f"Opens {fmt(start)} ET"
    if now <= end:
        return "open", f"Closes {fmt(end)} ET"
    return "closed", f"Ended {fmt(end)} ET"


def parse_walmart(d, s):
    cards = d.get("cards") or []
    if not cards:   # no product links found: fall back to reading the page line by line
        lines = [l.strip() for l in d.get("text", "").splitlines() if l.strip()]
        for i, l in enumerate(lines):
            if DRAW_RX.search(l) or WINDOW_RX.search(" ".join(lines[i:i + 3])) or CHANCE_OPEN.search(l):
                back = lines[max(0, i - 8):i + 1]
                name = next((x for x in reversed(back) if is_tcg(x)), None)
                if name and name not in {c["name"] for c in cards}:
                    start = max(j for j in range(max(0, i - 8), i + 1) if lines[j] == name)
                    cards.append({"href": WALMART_DRAW, "name": name, "text": "\n".join(lines[start:i + 3])})
    out = []
    for c in cards:
        if not is_tcg(c["name"]):          # Pokémon cards only (no plush, figures, toys)
            continue
        t = c.get("text", "")
        m = DRAW_RX.search(t)
        when = ""
        win = walmart_window(t)
        if win:
            status, when = win
        elif m:
            w = m.group(1).lower()
            status = ("closed" if w in ("ended", "closed") else
                      "open" if w in ("ends", "closes", "open", "is open", "now open") else "upcoming")
            when = ("Ends " if status == "open" else "Starts " if status == "upcoming" else "Ended ") + m.group(2).strip()
        elif CHANCE_OPEN.search(t) and not CHANCE_DONE.search(t):
            status = "open"
        elif CHANCE_DONE.search(t):
            status = "closed"
        elif _re.search(r"coming soon|opens soon|upcoming", t, _re.I) or CHANCE_SOON.search(t):
            status = "upcoming"
        else:
            status = "unknown"
        price = store_price(t)
        limit = msrp_limit(c["name"], s, "walmart")
        out.append({"name": c["name"][:160], "url": c.get("href") or WALMART_DRAW, "price": price, "limit": limit,
                    "status": status, "when": to_eastern(when)[:60],
                    "msrp_ok": limit is not None and not NOT_RETAIL.search(c["name"])
                               and (price is None or price <= limit + allowance_for(limit, s) + ROUNDING)})
    order = {"open": 0, "upcoming": 1, "unknown": 2, "closed": 3}
    out.sort(key=lambda x: order[x["status"]])
    closed = [x for x in out if x["status"] == "closed"][:4]
    return [x for x in out if x["status"] != "closed"] + closed


def checker_tab(name, url):
    """This checker's own Chrome window (minimized, so it stays out of your way)."""
    c = checkers[name]
    if c["tab"] and window_exists(c["tab"]):
        go_to(c["tab"], url)
        tab_do(c["tab"], """try
                                set minimized of WIN to true
                            end try""", timeout=10)      # Chrome sometimes pops it back open
        return c["tab"]
    out, err = osa(f'''
        tell application "Google Chrome"
            set w to make new window
            set URL of active tab of w to "{as_str(url)}"
            try
                set minimized of w to true
            end try
            return (id of w as text) & "," & (id of active tab of w as text)
        end tell''', timeout=60)
    parts = out.split(",")
    if len(parts) == 2 and all(p.isdigit() for p in parts):
        c["tab"] = (parts[0], parts[1])
        return c["tab"]
    raise RuntimeError("couldn't open a Chrome window" + (f" ({err[:80]})" if err else ""))


def close_checker_tab(name):
    c = checkers[name]
    if c["tab"]:
        tab_do(c["tab"], "close WIN", timeout=5)
        c["tab"] = None


def background_tab(url):
    """A new tab inside the minimized Pokémon Center window, so nothing pops up on screen.
    Returns ((window, tab), own_window). Falls back to a minimized window of its own."""
    pkc = find_tab(load_settings()["watch_url"])
    if pkc:
        out, _ = osa(f"""
            {IF_CHROME}
            tell application "Google Chrome"
                repeat with x in windows
                    if (id of x as text) is "{pkc[0]}" then
                        if minimized of x is false then return "{NOT_FOUND}"
                        set t to make new tab at end of tabs of x with properties {{URL:"{as_str(url)}"}}
                        return (id of x as text) & "," & (id of t as text)
                    end if
                end repeat
                return "{NOT_FOUND}"
            end tell""", timeout=30)
        parts = out.split(",")
        if len(parts) == 2 and all(p.isdigit() for p in parts):
            return (parts[0], parts[1]), False
    if not chrome_running():
        raise RuntimeError("Chrome isn't open, so it'll try again at the next check")
    return None, True


def read_in_tab(name, url):
    """Read the page in real Chrome (sites that block hidden readers let real Chrome in)."""
    tab, own = background_tab(url)
    if own:
        tab = checker_tab(name, url)
    try:
        wait_for_load(tab, checker_stop, min_wait=4, max_wait=30)
        err, seen = "", None
        for _ in range(3):
            out, err = run_js(tab, READ_JS)
            if out and not err:
                try:
                    d = json.loads(out)
                    if d.get("cards") or d.get("text", "").strip():
                        return d
                    seen = d
                except ValueError:
                    pass
            if checker_stop.wait(4):
                break
        if seen and seen.get("title"):
            return seen                         # e.g. a robot-check page: the caller recognizes it
        if "allow javascript" in err.lower() or "apple events" in err.lower():
            raise RuntimeError("Chrome wouldn't let it read the page")
        raise RuntimeError("the page didn't load. It'll try again at the next check")
    finally:
        if own:
            close_checker_tab(name)
        else:
            close_tab(tab)


def load_page(name, url):
    c = checkers[name]
    s = load_settings()
    if not s["checker_invisible"]:
        c["how"] = "its own minimized window"
        return read_in_window(name, url)
    if headless_available() and time.time() >= c["tab_until"]:
        why = ""
        try:
            d = read_headless(name, url)
            if CHECKER_BLOCK.search(d.get("title", "") + "\n" + d.get("text", "")[:3000]):
                why = "the site blocked the hidden reader"
            elif d.get("cards") or len(d.get("text", "")) > 200:
                c["hidden_fails"] = 0
                c["how"] = "hidden reader"
                return d
            else:
                why = "the page came back empty"
        except Exception as e:
            why = str(e).split(". It'll")[0]
        c["hidden_fails"] += 1
        site = SITE_NAMES.get(name, name.title())
        if c["hidden_fails"] >= 2:          # it keeps failing: use the background tab for the next 6 hours
            c["tab_until"] = time.time() + 6 * 3600
            c["hidden_fails"] = 0
        log(f"{site}: hidden reader didn't work ({why}). Reading it in a background tab instead.")
    c["how"] = "background tab"
    return read_in_tab(name, url)


def read_in_window(name, url):
    last = ""
    for attempt in range(2):
        tab = checker_tab(name, url)
        wait_for_load(tab, checker_stop, min_wait=4, max_wait=30)
        out, err = run_js(tab, READ_JS)
        if out and not err:
            try:
                return json.loads(out)
            except ValueError:
                pass
        last = err or ""
        close_checker_tab(name)                # start over with a fresh window
        if checker_stop.wait(3):
            break
    if "allow javascript" in last.lower() or "apple events" in last.lower():
        raise RuntimeError("Chrome wouldn't let it read the page")
    raise RuntimeError("couldn't load the page this time. It'll try again at the next check")


class Blocked(Exception):
    pass


def check_block(d):
    if CHECKER_BLOCK.search((d.get("title", "") + "\n" + d.get("text", "")[:3000])):
        raise Blocked()


def open_in_chrome(url):
    if str(url).startswith(("http://", "https://")):
        osa(f'''tell application "Google Chrome"
            set w to make new window
            set URL of active tab of w to "{as_str(url)}"
            activate
        end tell''', timeout=30)


def run_target(s):
    with checkers["target"]["lock"]:      # one check at a time, so pages never get mixed up
        _run_target(s)


def shop_discord(s, it, name, test=False):
    """Your store message with {item}, {price} and {link} filled in."""
    price = f"${it['price']:.2f}" if it.get("price") is not None else "(price not shown)"
    msg = (s[f"discord_{name}_msg"].replace("{item}", it["name"]).replace("{price}", price)
           .replace("{link}", f"<{it['url']}>"))
    return ("\U0001F9EA **TEST** (not a real restock):\n" + msg) if test else msg


def shop_alert(s, it, name, test=False):
    site = SITE_NAMES[name]
    price = f"${it['price']:.2f}" if it.get("price") is not None else "(price not shown)"
    who = s[f"{name}_mention"]
    ss = dict(s, discord_mention=who) if who in ("here", "everyone") else with_role(s, f"discord_role_{name}")
    send_all(ss, f"TEST: {site} alert" if test else f"{site} restock!",
             ("TEST: " if test else "") + f"{it['name']} is in stock at {site} for {price}.",
             shop_discord(s, it, name, test), discord_mention_on=(not test and who != "none"), channel=name)


def target_discord(s, it, test=False):
    return shop_discord(s, it, "target", test)


def target_alert(s, it, test=False):
    shop_alert(s, it, "target", test)


def bestbuy_alert(s, it, test=False):
    shop_alert(s, it, "bestbuy", test)


def seen_file(name):
    return BESTBUY_SEEN_FILE if name == "bestbuy" else os.path.join(DATA_DIR, f"{name}_invites_seen.json")


def load_invites_seen(name):
    try:
        with open(seen_file(name)) as f:
            return set(json.load(f))
    except Exception:
        return set()


def save_invites_seen(name, seen):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(seen_file(name), "w") as f:
        json.dump(sorted(seen)[-300:], f)


def load_bestbuy_seen():
    return load_invites_seen("bestbuy")


def save_bestbuy_seen(seen):
    save_invites_seen("bestbuy", seen)


INVITE_HOW = {"bestbuy": ("Request an invite", "If you're picked, you get 24 hours to buy."),
              "target": ("Enter the drawing", "If you're picked, you get a link to buy."),
              "walmart": ("Enter the drawing", "If you're picked, you get a link to buy."),
}


def shop_soon_alert(s, it, name):
    site = SITE_NAMES[name]
    price = f" ({money_str(it.get('price'))})" if it.get("price") is not None else ""
    send_all(with_role(s, f"discord_role_{name}"), f"{site} drawing coming up", f"{it['name']}{price}. Coming up soon.",
             f"\U0001F39F\uFE0F **{site} drawing/invite coming up:** {it['name']}{price}. "
             f"You'll get another ping when it opens.\n<{it['url']}>", channel=name)


def shop_invite_alert(s, it, name):
    """The store opened invite requests (a drawing for the chance to buy). Like a Walmart drawing."""
    site = SITE_NAMES[name]
    ask, after = INVITE_HOW.get(name, ("Request an invite", ""))
    price = f" ({money_str(it.get('price'))})" if it.get("price") is not None else ""
    who = s[f"{name}_mention"]
    ss = dict(s, discord_mention=who) if who in ("here", "everyone") else with_role(s, f"discord_role_{name}")
    send_all(ss, f"{site} invite is OPEN", f"{ask} now: {it['name']}{price}. {after}".strip(),
             f"\U0001F39F\uFE0F **{site} invite open:** {it['name']}{price}. {ask} for a chance to buy.\n<{it['url']}>",
             discord_mention_on=(who != "none"), channel=name)


def bestbuy_invite_alert(s, it):
    shop_invite_alert(s, it, "bestbuy")


def money_str(v):
    return f"${v:.2f}" if v is not None else ""


def shop_first_test(s, name):
    """Once, right after the store's first real check with its own channel: post a TEST in that channel
    showing what the check found, so you know it works. Never @'s anyone."""
    site = SITE_NAMES[name]
    with settings_lock:
        fresh = load_settings()
        if fresh[f"{name}_test_done"] or not fresh[f"discord_webhook_{name}"]:
            return False
        fresh[f"{name}_test_done"] = True
        save_settings(fresh)
    c = checkers[name]
    sample = next((i for i in c["items"] if i.get("price") is not None), None)
    lines = [f"\U0001F9EA **TEST:** the {site} checker is working (this is a one-time test, not a restock).",
             f"Just checked {site}: {c.get('message') or 'done'}."]
    if sample:
        lines.append(f"Example it read: {sample['name']} at {money_str(sample.get('price'))} <{sample['url']}>")
    lines.append(f"Real restocks and invite drawings will post here and @ the {site} role. "
                 f"It checks every {fresh[f'{name}_every']} minutes.")
    send_all(fresh, f"TEST: {site} checker works", f"{site} checked fine. A one-time TEST was posted in your "
             f"{site} Discord channel.", "\n".join(lines), discord_mention_on=False, channel=name)
    log(f"{site} works: posted a one-time TEST in its Discord channel.")
    return True


def bestbuy_first_test(s):
    return shop_first_test(s, "bestbuy")


def send_walmart_test(s):
    """A sample 'Walmart drawing open' alert to your phone and Discord. Never @'s anyone."""
    sample = next((i for i in checkers["walmart"]["items"] if i.get("status") in ("open", "upcoming")), None) or \
        {"name": "Pokémon TCG: Elite Trainer Box", "price": 59.99, "when": "Ends tonight"}
    price = money_str(sample.get("price"))
    send_all(s, "TEST: Walmart alert", f"TEST: Walmart drawing open: {sample['name']} {price}".strip(),
             f"\U0001F9EA **TEST** (not a real drawing):\n\U0001F39F\uFE0F **Walmart drawing open:** {sample['name']} {price} "
             f"({sample.get('when') or 'example'})\n<{WALMART_DRAW}>", discord_mention_on=False, channel="walmart")
    log("Sent a Walmart test alert.")
    return f"Sent a test alert for \"{sample['name'][:60]}\". It didn't @ anyone."


def send_pkc_test(s):
    """A sample queue message in the Pokémon Center channel (no @, opens nothing). The full drill is in the app."""
    send_all(s, "TEST: Pokémon Center alert", "TEST: Pokémon Center queue alerts are set up.",
             "\U0001F9EA **TEST** (not a real queue):\n" + discord_text(s, "discord_queue_msg"), discord_mention_on=False)
    log("Sent a Pokémon Center test alert.")
    return "Sent a test queue message to the Pokémon Center channel. It didn't @ anyone."


SHOP_TESTS = {"pkc": lambda s: send_pkc_test(s), "target": lambda s: send_target_test(s),
              "bestbuy": lambda s: send_bestbuy_test(s),
              "walmart": lambda s: send_walmart_test(s)}


def walmart_alert(s, it, test=False):
    shop_alert(s, it, "walmart", test)


def send_bestbuy_test(s):
    """Send a sample Best Buy alert to your phone and Discord. Never @'s anyone, opens nothing."""
    sample = next((i for i in checkers["bestbuy"]["items"] if i.get("price") is not None), None) or \
        {"name": "Pokémon Trading Card Game: Elite Trainer Box", "price": 59.99, "url": BESTBUY_SEARCH}
    bestbuy_alert(s, sample, test=True)
    log("Sent a Best Buy test alert.")
    return f"Sent a test alert for \"{sample['name'][:60]}\". It didn't @ anyone."


def send_target_test(s):
    """Send a sample Target alert to your phone and Discord. Never @'s anyone, opens nothing."""
    sample = next((i for i in checkers["target"]["items"] if i.get("price") is not None), None) or \
        {"name": "Pokémon Trading Card Game: 30th Celebration Elite Trainer Box", "price": 69.99,
         "url": "https://www.target.com/p/-/A-1010892076"}
    target_alert(s, sample, test=True)
    log("Sent a Target test alert.")
    return f"Sent a test alert for \"{sample['name'][:60]}\". It didn't @ anyone."


def run_bestbuy(s):
    with checkers["bestbuy"]["lock"]:
        _run_shop("bestbuy", s)


def _run_target(s):
    _run_shop("target", s)


SHOPS = {"target": {"search": TARGET_SEARCH, "product": parse_target_product, "scan": parse_target_search,
                    "alert": target_alert, "own": "Target's own price"},
         "bestbuy": {"search": BESTBUY_SEARCH, "product": parse_bestbuy_product, "scan": parse_bestbuy_search,
                     "alert": bestbuy_alert, "own": "Best Buy's own price"},
         "walmart": {"search": WALMART_SEARCH, "product": parse_walmart_product, "scan": parse_walmart_search,
                     "alert": walmart_alert, "own": "Walmart's own price"}}


def mark_chance(it, d):
    """Any store page with a drawing / raffle / lottery / invite for a Pokémon card product, whatever the price."""
    if it.get("status") in ("third", "notretail") or not is_tcg(it.get("name", "")):
        return it
    main = BB_OTHER.split(OTHER_PRODUCTS.split(d.get("text", ""), 1)[0], 1)[0]
    st = chance_state(main, d.get("buttons", []))
    if st == "open":
        it["status"] = "invite"
    elif st == "soon" and it.get("status") != "invite":
        it["status"] = "invite_soon"
    return it


def _run_shop(name, s):
    """Target and Best Buy: check your links + the Pokémon card search, alert for official cards at MSRP."""
    c, cfg, site = checkers[name], SHOPS[name], SITE_NAMES[name]
    urls = [u for u in s[f"{name}_watch"].splitlines() if u.strip()]
    if s[f"{name}_scan"]:
        urls.append(cfg["search"])
    if not urls:
        c["message"] = f"Add {site} links or turn on the search scan in {site} settings."
        c["items"] = []
        return
    items, seen, problems = [], set(), []
    scanned = None
    started = time.time()
    for i, url in enumerate(urls):
        if checker_stop.is_set():
            return
        c["message"] = f"Checking {i + 1} of {len(urls)}"
        try:
            d = load_page(name, url)
        except Blocked:
            raise
        except Exception as e:                 # one page failing doesn't throw away the rest
            problems.append(str(e).split(". It'll")[0])
            log(f"{site}: skipped one page this round ({problems[-1]}).")
            continue
        check_block(d)
        if url == cfg["search"]:
            found = []
            scanned = sum(1 for x in d.get("cards", []) if is_tcg(x.get("name", "")))
            chance_cards = [{"url": x["href"], "name": x["name"]} for x in d.get("cards", [])
                            if x.get("href") and is_tcg(x.get("name", "")) and chance_state(x.get("text", ""))]
            if not scanned:
                log(f"{site}: the search page showed no Pokémon card products this time "
                    f"(page title: \"{(d.get('title') or 'none')[:70]}\").")
            cands = cfg["scan"](d, s)[:5]
            cands += [x for x in chance_cards if x["url"] not in {y["url"] for y in cands}][:5]
            for cand in cands:                             # search results don't show the seller,
                if cand["url"] in seen:                    # so open each hit to double-check
                    continue
                if checker_stop.wait(random.uniform(*CHECK_GAP)):
                    return
                pd = load_page(name, cand["url"])
                check_block(pd)
                it = mark_chance(cfg["product"](pd, cand["url"], s), pd)
                it["from_scan"] = True
                if it["status"] in ("in", "invite", "invite_soon"):
                    found.append(it)
        else:
            it = mark_chance(cfg["product"](d, url, s), d)
            if it["status"] == "nolimit":        # official, sold by the store, just not in the price table
                it["status"] = "in"
                it["note"] = cfg["own"]
            found = [it]
        for it in found:
            if it["url"] not in seen:
                seen.add(it["url"])
                items.append(it)
        if i < len(urls) - 1 and checker_stop.wait(random.uniform(*CHECK_GAP)):
            return
    seen = load_invites_seen(name)               # every store: drawings, raffles and invites
    for it in items:
        if it["status"] == "invite" and it["url"] not in seen:
            seen.add(it["url"])
            log(f"{site} drawing/invite open: {it['name']}")
            shop_invite_alert(s, it, name)
        elif it["status"] == "invite_soon" and "soon|" + it["url"] not in seen:
            seen.add("soon|" + it["url"])
            log(f"{site} drawing/invite coming up: {it['name']}")
            shop_soon_alert(s, it, name)
    save_invites_seen(name, seen)
    for it in items:
        if it["status"] == "in":
            if it["url"] not in c["alerted"]:
                c["alerted"].add(it["url"])
                log(f"{site} restock: {it['name']} {it['price']}")
                cfg["alert"](s, it)
                if s[f"{name}_open_page"]:
                    open_in_chrome(it["url"])
        else:
            c["alerted"].discard(it["url"])
    instock = sum(1 for it in items if it["status"] == "in")
    invites = sum(1 for it in items if it["status"] in ("invite", "invite_soon"))
    c["items"] = items
    seen_txt = (f"{scanned} Pokémon products seen" if scanned is not None else f"{len(items)} checked")
    c["message"] = (f"{instock} in stock at MSRP!" if instock else f"Nothing in stock at MSRP ({seen_txt})")
    if invites:
        c["message"] += f", {invites} drawing/invite{'s' if invites > 1 else ''} open or coming up"
    if problems:
        c["message"] += f", {len(problems)} page{'s' if len(problems) > 1 else ''} didn't load"
    c["took"] = time.time() - started
    if c["took"] > 120:
        log(f"{site} check took {round(c['took'] / 60)} min ({len(urls)} pages).")


def load_walmart_seen():
    try:
        with open(WALMART_SEEN_FILE) as f:
            return set(json.load(f))
    except Exception:
        return set()


def save_walmart_seen(seen):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(WALMART_SEEN_FILE, "w") as f:
        json.dump(sorted(seen)[-300:], f)


def run_walmart(s):
    with checkers["walmart"]["lock"]:
        c = checkers["walmart"]
        prod_items, prod_msg = [], ""
        if s.get("walmart_products"):
            _run_shop("walmart", s)
            prod_items, prod_msg = list(c["items"]), c["message"]
            if checker_stop.wait(random.uniform(*CHECK_GAP)):
                return
        _run_walmart(s)
        if s.get("walmart_products"):
            c["items"] = c["items"] + prod_items
            c["message"] = f"Drawings: {c['message']} · Products: {prod_msg}"


def _run_walmart(s):
    c = checkers["walmart"]
    d = load_page("walmart", WALMART_DRAW)
    check_block(d)
    items = parse_walmart(d, s)
    seen = load_walmart_seen()
    for it in items:
        if it["status"] == "unknown":
            it["status"], it["when"] = "upcoming", it["when"] or "time not shown yet"
        if it["status"] not in ("open", "upcoming"):
            continue
        # Once per item per day for "coming up", then once more when it opens.
        key = f"{it['name']}|{it['status']}|{eastern_now():%Y-%m-%d}"
        if key in seen:
            continue
        seen.add(key)
        price = f"${it['price']:.2f}" if it["price"] is not None else ""
        if it["status"] == "open":
            log(f"Walmart drawing open: {it['name']}")
            send_all(with_role(s, "discord_role_walmart"), "Walmart drawing is OPEN", f"Enter now: {it['name']} {price}. {it['when']}".strip(),
                     f"\U0001F39F️ **Walmart drawing open:** {it['name']} {price} ({it['when']})\n<{WALMART_DRAW}>",
                     channel="walmart")
        else:
            log(f"Walmart drawing coming up: {it['name']}")
            send_all(with_role(s, "discord_role_walmart"), "Walmart drawing coming up",
                     f"{it['name']} {price}. {it['when']}".strip(),
                     f"\U0001F39F️ **Walmart drawing coming up:** {it['name']} {price} ({it['when']}). "
                     f"You'll get another ping when it opens.\n<{WALMART_DRAW}>",
                     channel="walmart")
    save_walmart_seen(seen)
    c["items"] = items
    live = [x for x in items if x["status"] in ("open", "upcoming")]
    c["message"] = (f"{sum(x['status'] == 'open' for x in live)} open, "
                    f"{sum(x['status'] == 'upcoming' for x in live)} coming up" if live
                    else "No Pokémon drawings open or coming up")


def overnight_now():
    now = eastern_now()
    mins = now.hour * 60 + now.minute
    return 30 <= mins < 240     # 12:30 AM - 4:00 AM Eastern


def checker_loop(name):
    c = checkers[name]
    run = {"target": run_target, "walmart": run_walmart, "bestbuy": run_bestbuy}[name]
    while not checker_stop.is_set():
        try:
            s = load_settings()
            if not s[f"{name}_enabled"]:
                if c["tab"]:
                    close_checker_tab(name)
                c.update(status="off", message="Off", next=None)
                c["wake"].wait(15)
                c["wake"].clear()
                continue
            now = time.time()
            if health_paused():
                c.update(status="paused", message="Paused to let your computer cool down")
                c["wake"].wait(60)
                continue
            if now < c["paused_until"]:
                c.update(status="paused",
                         message=f"Paused: the site showed a robot check. Trying again at "
                                 f"{datetime.fromtimestamp(c['paused_until']).strftime('%I:%M %p').lstrip('0')}.")
                c["wake"].wait(min(60, c["paused_until"] - now))
                if c["wake"].is_set():
                    c["wake"].clear()
                    c["paused_until"] = 0
                continue
            if name == "target" and s["target_hours"] == "overnight" and not overnight_now() \
                    and not c["wake"].is_set():
                c.update(status="waiting", message="Waiting for overnight hours (12:30–4 AM)", next=None)
                if c["tab"]:
                    close_checker_tab(name)
                c["wake"].wait(60)
                continue
            if c["next"] is None or now >= c["next"] or c["wake"].is_set():
                c["wake"].clear()
                c.update(status="checking", message="Checking...")
                try:
                    run(s)
                    c["status"] = "on"
                    if name == "bestbuy":
                        shop_first_test(s, name)
                    c["block_pinged"] = False
                    c["last_result"], c["last_done"] = c["message"], time.time()
                except Blocked:
                    c["paused_until"] = time.time() + BLOCK_PAUSE
                    site = SITE_NAMES.get(name, name.title())
                    log(f"{site} showed a robot check. Pausing that checker for 30 minutes.")
                    if not c["block_pinged"]:
                        c["block_pinged"] = True
                        send_all(s, f"{site} checker paused",
                                 f"{site} showed a robot check, so that checker is resting for 30 minutes. "
                                 "Your other checkers keep running.")
                    close_checker_tab(name)
                    continue
                except Exception as e:
                    c.update(status="error", message=f"Problem: {e}")
                    log(f"{SITE_NAMES.get(name, name.title())} checker problem: {e}")
                c["last"] = time.time()
                every = max({"target": 2, "walmart": 10, "bestbuy": 3}[name], int(s[f"{name}_every"]))
                if name == "walmart" and any(i.get("status") == "upcoming" for i in c["items"]):
                    every = min(every, 3)   # a drawing is coming up: check every ~3 min so the "open" ping is fast
                c["next"] = time.time() + every * 60 * random.uniform(0.9, 1.15) * health_factor()
            c["wake"].wait(10)
        except Exception as e:
            log(f"{name.title()} checker error: {e}")
            time.sleep(30)


def checkers_info():
    out = {}
    for n, c in checkers.items():
        d = {k: c[k] for k in ("status", "message", "last", "next", "items")}
        if c["status"] == "checking" and c.get("last_result"):
            d["message"] = f"{c['message']} (last: {c['last_result']} at {short_time(c['last_done'])})"
        out[n] = d
    return out


# ---------------- schedule ----------------
# Drop hours: past Pokemon Center queues opened on weekdays, about 10 AM - 2 PM Eastern
# (mostly 11 AM - 1 PM, busiest Mon-Wed, none logged on weekends). This window covers them
# with a little room on each side.
SMART_WEEK = {str(d): {"on": d < 5, "start": "09:45", "end": "14:30"} for d in range(7)}
DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
SCHED_TICK = 15
sched = {"auto": False, "started_key": None, "awake": None}


def eastern_now():
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("America/New_York"))
    except Exception:
        return datetime.now()


def week_for(s):
    return SMART_WEEK if s["schedule_mode"] == "smart" else s["schedule_custom"]


def hm(t):
    h, m = t.split(":")
    return int(h) * 60 + int(m)


def nice_time(t):
    h, m = divmod(hm(t), 60)
    return f"{(h % 12) or 12}:{m:02d} {'AM' if h < 12 else 'PM'}"


def current_window(s, now):
    """The scheduled window happening right now, as (key, end time), or None."""
    if s["schedule_mode"] == "manual":
        return None
    day = week_for(s)[str(now.weekday())]
    mins = now.hour * 60 + now.minute
    if day["on"] and hm(day["start"]) <= mins < hm(day["end"]):
        return f"{now.date()} {day['start']}", day["end"]
    return None


def next_window(s, now):
    """Text like 'Tomorrow 9:45 AM' for the next scheduled start."""
    if s["schedule_mode"] == "manual":
        return None
    week = week_for(s)
    mins = now.hour * 60 + now.minute
    for offset in range(8):
        d = (now.weekday() + offset) % 7
        day = week[str(d)]
        if not day["on"] or hm(day["end"]) <= hm(day["start"]):
            continue
        if offset == 0 and hm(day["start"]) <= mins:
            continue
        when = "Today" if offset == 0 else "Tomorrow" if offset == 1 else DAY_NAMES[d]
        return f"{when} {nice_time(day['start'])}"
    return None


def keep_awake(on):
    """While a schedule is set, keep the Mac from going to sleep (the screen can still turn off)."""
    proc = sched["awake"]
    if on and not (proc and proc.poll() is None):
        try:
            sched["awake"] = subprocess.Popen(["caffeinate", "-i", "-w", str(os.getpid())])
        except Exception:
            sched["awake"] = None
    elif not on and proc:
        proc.terminate()
        sched["awake"] = None


def schedule_info():
    s = load_settings()
    now = eastern_now()
    win = current_window(s, now)
    running = bool(watcher_thread and watcher_thread.is_alive())
    return {
        "mode": s["schedule_mode"],
        "in_window": bool(win),
        "window_end": nice_time(win[1]) if win else None,
        "held": bool(win and not running and sched["started_key"] == win[0]),
        "auto": sched["auto"] and running,
        "next": next_window(s, now),
    }


def scheduler_tick():
    s = load_settings()
    keep_awake(s["schedule_mode"] != "manual")
    if s["schedule_mode"] == "manual":
        sched["auto"] = False
        return
    win = current_window(s, eastern_now())
    running = bool(watcher_thread and watcher_thread.is_alive())
    if win:
        # Start once per window. If it stops (queue found, block page, or you pressed Stop),
        # it waits for the next window, so it never refreshes you out of a queue.
        if health["level"] == "critical":        # paused to let the computer cool down
            return
        if not running and sched["started_key"] != win[0]:
            sched["started_key"] = win[0]
            log(f"Schedule: starting (until {nice_time(win[1])}).")
            start_watching()
            sched["auto"] = True
    elif sched["auto"]:
        sched["auto"] = False
        if running and state["status"] == "watching":
            log("Schedule: time's up, stopping until the next window.")
            stop_watching()


def scheduler_loop():
    while True:
        try:
            scheduler_tick()
        except Exception as e:
            log(f"Schedule error: {e}")
        time.sleep(SCHED_TICK)


def manual_start():
    """You pressed Start: run now, and don't let the schedule stop this run."""
    started = start_watching()
    if started:
        sched["auto"] = False
        win = current_window(load_settings(), eastern_now())
        if win:
            sched["started_key"] = win[0]
    return started


DRILL_URL = f"http://127.0.0.1:{PORT}/drill"
drill = {"hits": 0}


def start_drill():
    """Queue drill: the real watcher, real Chrome and real alerts, on a fake store page
    that turns into a queue on its second load (about 10 seconds in)."""
    drill["hits"] = 0
    return start_watching(test=True)


def start_watching(test=False):
    global watcher_thread
    if watcher_thread and watcher_thread.is_alive():
        if state["status"] == "watching":
            return False
        # Still sending queue reminders: end them, then start fresh.
        ack_event.set()
        stop_event.set()
        watcher_thread.join(timeout=15)
    stop_event.clear()
    s = load_settings()
    if test:
        s.update(watch_url=DRILL_URL, min_wait=5, max_wait=5, check_signed_in=False, _test=True)
        log("Queue drill started (fake store page, real alerts marked TEST).")
    set_state(status="watching", message="Queue drill: watching a fake store..." if test else "Starting...",
              js_warning=False)
    watcher_thread = threading.Thread(target=watch, args=(s, stop_event), daemon=True)
    watcher_thread.start()
    return True


def stop_watching():
    stop_event.set()
    ack_event.set()
    if not (watcher_thread and watcher_thread.is_alive()):
        set_state(status="idle", message="Ready. Press Start to begin watching.")


# ---------------- control panel ----------------
PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Guardian Monitor</title>
<link rel="icon" type="image/png" href="data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAEAAAABACAYAAACqaXHeAAAWJElEQVR4nO2ba5BeR3nnf0+fy3ubyzszGll332TLluVIsoRNsg7CBEMwoVKVArJrakkVuZB449TuVmqXqmzZOOs4eANssaG2kgqGzRbYG0xgTRI72AlYFLe1scEGjSRLtqy75NHMO5f3fk73sx/6vJcZzYxkLQkf1q06M3PO6dP99L/7+T+XbsEb5Y3yRvn/ucglf3nvvYannza79lYFnvsJivR6yi6e2zOgvPWtjvvuc/88fe7ZEwIx/y/g/eSLAHEm2+uS63VUfl8Aj4ZAi0/dnSt9t75lcGpuZ1BvXoOzoyJi0L5JEAMKsPjZons4/1n/PcZL2ffMiUFUnYamYvOFQ83y8A9ntyYHuO8vm0AO3pfCo/ZiRnVxAOzaFfHcc8Ldd8vYqzO/WHzt3G+aWu0tQWoHUc1aErp/d+8h+7F8HVjiu757JANgmToi2NBUXWngW83Vq/5i8ibzOPf9pWPrVpiYaF9oaBcGYPPmHIcPa/5Dd60d23/0gXCmcqexDucs6qdXAf0pASDZnTEmQE1AMjL0xXNXX/2R1uc/fZLNm4XDh1uXDsDWrTETE1L6l7+xeeSlIw+H89WfcTZ1GhhFMdL//U9rBfgeFMGJdWJMYJLBwYnKNZvurH/xcwfYulVXWgkrALAnhL1R7ld/7bKxl078TTQ3v82iCUKUSbSopQsMTgwYAaden50u7N3Po//tNPtOLxKkzlAUVFODhMng0MHpa674pdajD51kz56EvXvT1wOAAAX27DGrG4WHclPT73c2TREJlxVgKQA6wqcptFv+vTFoLgf5HBrHvmq7Dc0m0mqDcx6sXAxB2Bv8hQBY8ExTI2HYHis/dlZmPsgzz6RAkwWM7Eu45PC3bo2YmLDDwegdUeX0+61NLUi4YNIXT0D/MxE/iHoNkja6ejXuph3oNdeil62BwSHI57NVIGgceQDmZpBTJ5GDBzEvvYScm4IwgkIhYxq9uP6R0Kq1YWXul0c2bvilCs98ia1bw6VUYakV4Gd/+/b4Mh35q3B2/h1O1IrToFdj8Wz3NWUMWq8Bitu+HXvrrbB2LbTbyPQMUplBWtlqyOXRfB7iGB0cRMtldGQESkVo1pH9EwRPP438+EeIU8gXEdeZRF0IwmJeMGKNE5MMD3zztVLrV/je95pAYzF056+APXsC9u7Vwvim63h18lbnFDViFgK+CDcFggBsCjMV3E07se/5ZXAO8+wzmMf/HqyDfMHPZqmIFoswqCgCqfWrodFEpisQ59DhIdz23dhb34oc2E/4xUcwL7yA5otIEKJ2sZlfLJMYqypSb745P77mhiZ8PxvbAi44H4CTJwNAo/lkp6S26FCHYs7Tuf77IIBGHXIRye/9WxhfRfDVxzA/+IEXbGgIBgbRKPJ8kKRokkJqUWfBGT8tJoAoQnOxB+zMWZiu4K64mvb9D2L+4Umi//EQtlbHxHlYDMJCGQXUSepy+VrzpiY8m41tRQCEw4cNYEw7uRqboprR9WLTpNkSDAOYncZt2UL6m7+N+d53CD/9KcQEaLHkSa3dgkaARiGEIViLOoeqW7iSu4SfdRnHnitmZ9FqFffOO6jdsI3yJz5GMrEfHRjqA2FJGVVcirSSq4GQw4cdi0yY4fxigFCcK3fZd7krMGhlivTmW0juupvgM39O8MjDUCyh+aIXzmUmL03BOj9w5wFAFf8PVAQ1BmcCnAnQIECNoIDL5TClIvWjx9i+aT23/Ml/YX73m5CZaW86V5LRKWLTMsMsGScsZQUEMOrUONcH1SILoEEAMxXsz/0L0l+9k+j+P0QmJ9GRcT9Y0/nY96mtFrRa6NAwbnAYRkZguIwrFiEMkTjGhhFGBBHFicGZCBOESBDQaCVcuXY1N0/8gIc/9d8Jfudu2nGO4OlvQHkUsUsHg86BOgTFXCwAXakV8SS1+LsgQGtV3LYbSf7VB4j/833I3DyUhiBNUcm+MQLtNgi4devQK69CR0Yhl4c4QqMQDUMoFnGFAqW8pRCmREGLNgl1o9SDAqrKUDHPu+df48sPfpLmmdeI/+gPaf2nj8LsLObHP4bS4CJOyGSWzEQtOZDlAcA5h1qLSqY2fcGH2hQdHKD9G79F9MmPw7kpKA2iSdLrVARaTdzq1ejll0M7QY4fQ06cgGIRHRyE0TEYHkbjPMNlGBwU4oGYKsMMF5vkgoBAIyoyxvsLytd//34mT5yhODCInZ4h/PiDtH//PxLffx8yX0NMsEBOv1Qdzi2fKliKA7KiXd8jc0q9voqgtSrtD/8O5vG/wxx8CUoDHpTON87ikjbptm249RuQA/sxP/wBcuIkzM2h7bbnA1WsCrFJKAUtItekUD3DL0ZPEsxPUQjaCHXuHDHs+/gn2f+jAxTzBWw7gWIJ88oRgv/9Zdofvgtt1FGhJ0NX9s7d6wTAc5QuvMSgczOkt92GCgR/+1V0ZBRtJz3ecRanjnTXbrRWwzz7DFTraJzz7N5ooq22N4POgxAaSy5IaLYsn975JT596+d58Kr/xWuTNd69tsDst5/mW09/l8FigcRaP8h2gpZHMX//BNpqkrz9dnR2xsvYkRft8OClrABwqjjU/1bFOYvN50ne+S6Ch7+A5os463Cdulmd9KZdcPIE5tAhXKGIMwbn/FJ0aYJrJ/5vfHJDjfGsHwQExuF9oybXripT3vuPfP0LjxLfcguJtdDpB3DWosUBgoe/QPr227EDJZy1PVmya6WyNAcM9S35LlQG5mdJ3/UuOHUKOXQILY/2iMcYaNSxO3cip88gx4/jiqWFxCT4+3YTTdqoUzBC20TUBXIDwl0T/5qdJ/dzUH6eG8/u4yuf+2tk0kESk964neD55703mYFBLo8ceQWOvkrytl8geuwxGCov8AlWSg0trwLqUKe9yzqcCOnNb8Y89SQa5dDU+ncKNJrY9RvQ1CEvH0Zzhd77Tp3Uoq0Wms/jxldjx1ZBeZj6wDjz8Qjt/BDThTV83fwKu52y96FHqM5DmyLBkZfRVhu7cSPaaGTqpmiSonEB8+ST2F1vwoUhahfKziWRoGNhGNqo4zZfgzqH2b/fm7LMmcE5XBjg1q3DHJiAONeboc737Ra2UMBefz1uuAz1OtSqaDuBMGC6sJbp/Frm8uO8pxzxrT//K46fbFKrR4hL0TiPObAft2aNjx47fatCnMMceskDtGWLj0Lpk//SOKCf/ECbDdIdO5GDB/wskpEk3smx6zfA1BTUG4uIKHu/ahXuyqvg2DHkxRfglZehMg3NJpokBEaY1kFu27yd7/zPr/Gdbx8isUVUbSaDQLMJk+ew6zeirWZPBlVop8j+CeyOnf6dSE+GS1oBJovps0ujGL3ySszBg0iu4Ot03oUhjI1hzp71K4PsW2PAOtzoCLp+A2bfPky1hoQR0m4jqUXDkLBYoqVwx9VrqX3us3z7wDEGNl7ho0sTZNljgVwec/YMjI6iUdzLO4hAvoA5eBB3+RU+4SJ98psVhrnsm64K4L27wUG0UMQcPw5R1EtbpSkMDECaIvPVLK3d88MBdOMmD5ziB2SdV4FGkyCKaKhy81UbGH/qCf7moc8zeOI46Zq1qJheP6ogBqnWoN1GB32fPlhQCCPMyRM+lB4ahiTtOQMrbJmsaAa7JU3RkTIkCTI76wehPpuD9eBQry8kGxFIEtz4KpivIrWaD5szTpB2G9Nq0LSOqy/fwO7D+/jin/w3CsUSOl+FuVl0fBySdi+1Rjages1nlVLbS5gGBpmbg3YLHR2BNLmooV0YAMkAGC77TE6r1es0I0AtFJF6vSdgzytCh4Yw01N+8B3iAsQpyXSF0bEyd2idL937gE8ZOgVjMFNT6NBgn0vXI1Sp1dFCAbSPCBHvbjeaaLnsV4ec5/qfV5aNBQCk49OLIIUC0m4hUQxRjIjvQNUh+TzSbCBxDFHohTYGDQySyyHOIXGuB4IILk3JB8J7r1rL1x78BJVqg2KhiEMQIz6Ei2Mo5L2P7xydcEbA5wniGIkiH1aHIRJFSLsFhUJX9g4VvH4ATIcA6cXcaYqWBnqOiICGAeRiNI7RUsknSBRPjqjP8OTznjcwdGJMl6YMxhHf+LOHOHL0FIXRUax1mXo51AhEWZs+uePlss6TXKe/KOqF5zbxaqEd+TO0VkBgeQCyQBqla9M1l0cq0z5BmS0xaTWxtZpfATMzEMV00t+SJtjxcaRWQ5pNn+bOeCJSx9zxlMrR4xSTBNds+Zk1xrN/Loc26sh0BQnD7sohaSOFHFovYCoVb3VUkTBEZ6YhFyPqFvohujwLrqgC3TA4CJD5eZ+rj6PMtAReoCDwrJwvZJsbQRcASKHVRoeGkEbT1+0kSJxgwpBADC6xPVMVmIxYh7xOo73vsmWt+aLPM5hgYX9RhObyMDeffUO3v+XKCtrRSaAIBJGf3VzeD7TD9v2mqVBc4HGJgpgQc+4cOjaezcT52WRdaofHKbpqHDM1BSZcGM4pUCz6Prs70HgyzuW9/ldm/H5CL5C5BAAMPR2KIp+YBHT1ar+8OjoWRcj8PFos+mxutl2ZiqMdClKtQtLGbtoErWwVdMm1wzPSW02tJm7DBrCpN2tR1Fffb6Josdj3jkxtLDq+CjUmU8WoK8tK1mBZAEyHdNVbA1OrY86dw111FbSaCJK1b5BGw29vjYwiSYLiKEvIOsmhYUDw8itQLuM2rIdGHbH2vPyUOOvjjbVr0bExgpdf9h6j6+WyJGlDeQRJU6ReR4zptdNqeU+1UsFU571cqllu7xISIkBvCSmIBAQT+3DXb80sgHS9MDEB5uQp3Lr1BFap2jb/oXQjX131DpouJRBDODEBQ0O4667HlUo+M5ymnvCcRYtF3HXXwcgI4cREB94+OfwGilu/HnPqdGYatWtxSBPs1hswExMIpvfNyumAlcxgB3Y8i5aKBC+8QHr7O9BVY0izz0OLY8zUOdyGDaRrL6N45hSfaRzir5uvkpMANb4xc/AgOjaGrl/ng5tMHTRNAUWmpvx+YNDx/7W3jFtN3JrLvJN07jUfcXa4waXoyAi66XKCr3wZisWMn7KxrMCDF7ACfSWKkNcmkVMnSX/uZ4n/9u9gcLjn3UUxwaGD2B07CaenOZzOkQIlE3bTKhLFyNQUnDuHRhFaHvamdGYGaSde0jjOSLYvuakODQPc5msIXnxhIcEZA/NzpO+8HZl8DXP6NJRHV86D9ZXlVaC7X2+6bCvFEuFTT2F//i24fF/EhfjcfqOFOXKE9KabiJ0wYCK0E8116kUxxHmv242Wv6xC7D27biTZMbWApin2pl2YY0eResMD0DGLRnBxiH3rbYRPPYXkiz216LZzCSTYjbL6I7tcnmDffuTsWdK3345WKr5xVR/hRTHm5CnkzBnSXbuwnXMBnZ3bTjsLdlwyou3sIHUjP7+noEmC3bUbmZzEHD/hAbSua/u1UiG97W1QmSF48UW/Advfzk8kGuwUp0hpgOiRR0jf8U50fMynuPtPcRQKBEdexZw6ib35ZtzwMDRqPU9O+sgt+ytRS1NtL/ZwDuo1dGgQe8stmDNnvCUpFHqMnkWbOlImvePdxA8/jBRKF730LwoAWXypIlFMcPoM0ROP0/7du5HqHJgeZ4tTJF8gOHGSYN8+dPNm3LYboZj3wVSridgEcQ6jYNWxzuTYIkVsq54FMznctm24a68l2D9BcPw4ki8sMIkYA3MztP/N7xJ9/R99nTjXNX3910qDXOGd6AI96nKBQnmM6PEnYH6e1m99GKY8qS3Q9XwRU28QPPc8Mj+Pve560h07sFdeiRsZRQt5JI5oRMLPlq/kvRt2U7tiI7pjJ/b6rVCrETz3HKZah3yRfm7QOIapc7Q+9OuQpkSPfRVGxrqe6RJXn0FfWJawApcpc2edbpTZ7kbyAg4RxFp0qEzuT/+U5r330v7AB4gffgQdG0c6aXBVyDY25cRJ5OQpb6rKZdz6dRCEpHFEQYRH2y3UnqNUK6LHjxFUKj7g6pBinyOjQQCTZ2m//324rVvJ33sPDA5nm6Pnu9ogaBDMMb80E5wPwFqjnAaNglezxEe/N9IHAxDnyP/R/TTvuYd2Lib+7GdhZNT77x2h1Q9EUJ9NmjrnH4v4tJYYwrm5Xv0wO0PQGXhnTEbAWTg3SfuDH8Tu2EH+vo96b7Er3+IJVu8TReZV/3Ktg9MLaixWAWVkJAVMO7L7XBjWs23l85lFFQlCxITkP/pR3IaNNP/gD/wByvm5ns/fAUHx4XAu7zczwqjrThNG3ecEUS8LBN0YQatzuLRN8yMfwV29mfw99yAYJIiXc3UVxbggaLVifgQYNpfs4rGczwETEw4Qe3b/K1os/B/xLmEvHdN/KUgQIXGR/McexBw9SuOBPyZ98y3obAVtNDxZdTO7fRPVcVU77mr/BHZ8AGOg2UBnKqS7d9P8449hzpwh/8ADSJTvG/wSsiFOMGgx/31beekQIBw+fN4m0VIegmFsrMTUVCl38+3vLpyY+Qxp26ISLPTO+g25eN2sTGG3XEv7A3dCkhI98TjBiz9CktTb5zjXWxXOoYW89wTr9a7Tg6r3HZoNNAyxN95A8q47IJ8nfuQRgokJGBnzOr9Iht69gKgliIPGuuG7Ws/9w1cYHa0zPV1lkVewFAACRMAo61cNDgxt+0Q0OfMexaZwgYOSRvysJy2SW28l/YW3gSjBiy8SvPhjzOmzSLXqCU4VNzjgffvZOQ+iEXSghFtzGfbGG7Dbt4MEhN94muib34QwQgrFvpOki/rvPtNUCMJ01fDX5luHfo8jp+aAaSBhkQos5yMGMF6AydFw15s3lirhQ2Zmfosa9SCsdFIzyyForQoodsu1pDfvxl2xyafJKrM+zTU76z068GeNhobQsVF0xMcX5uhxgme/T3jgJa9qpYEeN6x4UlRTcYRueOjl2ir7ofTZ7x5lbGyaqak6S+yTLgeAACGDg0PMzw8Hb9pz7cCU/a9mtnqd4hzGKKoLz9wsFspkCdV6HW020UIeXXsZbu1a3Pg4Wh6CrlY5ZHYWMzmJOX3G7y7Xm0g+B8WSb6//DND5AKg/WOQQjHGDpcPV8fDf2Wf3TjAwMEe1Oos/HnceW64QKCJATKk0TK02xK5bNgzViv8+mJl/T+eYG4I/frSUUH1ZGO38J4hObJCmdDYzuoMTyaxEzptBMT652W1kGX33jp+RjDhteeCJWqn1cfv8d49RKs1Tq83gl/6SfsBKAIC3EjEMDEJ1mPWri4U1O94SVZP3Sq2+y6R2YEHwshQAC551Yvwl0lT97SzeYVoOgKwdFwY1LRWeT4aiLzVO/XAvJydrUJqD2hzQXm7wFwNAB4QIhoswOwxErFs1EK+69qpQ4+sk1U3G6TCoLOimY2CXenahclHtiDrDnIZyLA3aB9qVQy9z9FwVSGB4FmbrrDDznXIxAHTqhUAEAyWoFrN7AxhiguWOof0TFb+70sbiiU2BFAbqUK3jZ31JnV9cXo/AnQEGQMjISEwliaAaZs/+uQbfKQpYGLCMRG0qlc6gLReY9f5yqUJ3osx+1+unUfp9yL482hvljfJGucjyfwF2suG46nJNFQAAAABJRU5ErkJggg==">
<style>
:root{--bg:#f4f5f7;--card:#fff;--text:#1b1d22;--muted:#6b7080;--line:#e2e4ea;
--go:#1f9d55;--stop:#d64545;--accent:#2f6fde;--found:#f2b705;--warn-bg:#fff6d6;--warn-text:#6b5200}
@media (prefers-color-scheme:dark){:root{--bg:#15171c;--card:#1f2229;--text:#eceef3;--muted:#9aa0ae;
--line:#2e323b;--warn-bg:#3a3214;--warn-text:#f3dc8a}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);font:15px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
.wrap{max-width:600px;margin:0 auto;padding:24px 16px 40px}
h1{font-size:22px;margin:0 0 16px}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:18px;margin-bottom:14px}
.status{display:flex;align-items:center;gap:12px}
.dot{width:14px;height:14px;border-radius:50%;background:var(--muted);flex:none}
.watching .dot{background:var(--go);animation:pulse 1.4s infinite}
.found .dot{background:var(--found)}
.error .dot{background:var(--stop)}
@keyframes pulse{50%{opacity:.35}}
.big{font-size:18px;font-weight:600}
.sub{color:var(--muted);font-size:13px;margin-top:2px}
.found.card{border-color:var(--found);box-shadow:0 0 0 3px color-mix(in srgb,var(--found) 30%,transparent)}
.btns{display:flex;gap:10px;margin-top:16px;flex-wrap:wrap}
button{font:inherit;font-weight:600;border:0;border-radius:10px;padding:12px 18px;cursor:pointer;color:#fff}
button:disabled{opacity:.4;cursor:default}
.start{background:var(--go);flex:1}.stopb{background:var(--stop);flex:1}
.plain{background:transparent;color:var(--accent);border:1px solid var(--line)}
.warn{background:var(--warn-bg);color:var(--warn-text);border-radius:10px;padding:12px;margin-top:14px;font-size:14px;display:none}
label{display:block;font-size:13px;color:var(--muted);margin:12px 0 4px}
input,select,textarea{width:100%;font:inherit;padding:10px;border-radius:8px;border:1px solid var(--line);background:var(--bg);color:var(--text)}
.row{display:flex;gap:10px}.row>div{flex:1}
summary{cursor:pointer;font-weight:600}
.topic{font-family:ui-monospace,Menlo,monospace;background:var(--bg);padding:3px 7px;border-radius:6px}
ol{padding-left:20px;margin:8px 0 0}li{margin:4px 0}
.log{font-family:ui-monospace,Menlo,monospace;font-size:12px;color:var(--muted);max-height:180px;overflow:auto;white-space:pre-wrap;margin-top:8px}
.saved{color:var(--go);font-size:13px;margin-left:8px}
label.check{display:flex;gap:8px;align-items:center;color:var(--text);font-size:15px}
label.check input{width:auto;margin:0}
textarea{resize:vertical}

/* ===== Themes ===== */
:root{--glow:transparent;--particle:#2f6fde;--card-blur:none;--head-font:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;--btn-text:#fff}
:root[data-theme="neon"]{--bg:#060a13;--card:rgba(12,20,36,.62);--text:#e3f6ff;--muted:#7f9bb3;--line:rgba(0,229,255,.22);
--go:#00f5a0;--stop:#ff2e88;--accent:#00e5ff;--found:#ffd60a;--warn-bg:rgba(255,214,10,.12);--warn-text:#ffe680;
--glow:rgba(0,229,255,.45);--particle:#00e5ff;--btn-text:#04121a}
:root[data-theme="matrix"]{--bg:#010803;--card:rgba(4,22,10,.66);--text:#c9ffd2;--muted:#5fa36c;--line:rgba(57,255,20,.22);
--go:#39ff14;--stop:#ff4d4d;--accent:#39ff14;--found:#e6ff00;--warn-bg:rgba(230,255,0,.1);--warn-text:#efff8a;
--glow:rgba(57,255,20,.4);--particle:#39ff14;--btn-text:#021a06}
:root[data-theme="synth"]{--bg:#10001f;--card:rgba(38,6,64,.58);--text:#ffe9ff;--muted:#b98bd1;--line:rgba(255,78,205,.25);
--go:#00f0ff;--stop:#ff3864;--accent:#ff4ecd;--found:#ffb300;--warn-bg:rgba(255,179,0,.12);--warn-text:#ffd27a;
--glow:rgba(255,78,205,.45);--particle:#ff4ecd;--btn-text:#16002a}
:root[data-theme="ice"]{--bg:#050f1c;--card:rgba(14,32,54,.6);--text:#e8f4ff;--muted:#8fb3cf;--line:rgba(125,211,252,.24);
--go:#34d399;--stop:#fb7185;--accent:#7dd3fc;--found:#fde047;--warn-bg:rgba(253,224,71,.1);--warn-text:#fdf08a;
--glow:rgba(125,211,252,.4);--particle:#bae6fd;--btn-text:#04182a}
:root[data-theme="ember"]{--bg:#120604;--card:rgba(40,14,8,.62);--text:#fff1e8;--muted:#c9927a;--line:rgba(255,106,61,.25);
--go:#ffb703;--stop:#ff3d3d;--accent:#ff6a3d;--found:#ffe14d;--warn-bg:rgba(255,225,77,.1);--warn-text:#ffe9a0;
--glow:rgba(255,106,61,.45);--particle:#ff8a50;--btn-text:#1f0800}
:root:not([data-theme="classic"]){--card-blur:blur(12px) saturate(140%);--head-font:ui-monospace,"SF Mono",Menlo,monospace;color-scheme:dark}

#fx{position:fixed;inset:0;width:100%;height:100%;z-index:0;pointer-events:none}
#wp{position:fixed;inset:0;z-index:0;pointer-events:none;display:none}
#wp img,#wp video{width:100%;height:100%;object-fit:cover;display:none}
#wp::after{content:"";position:absolute;inset:0;background:linear-gradient(rgba(0,0,0,.35),rgba(0,0,0,.6))}
.bgs{display:grid;grid-template-columns:repeat(2,1fr);gap:8px;margin-top:10px}
.bgb{padding:10px 12px;border-radius:10px;border:1px solid var(--line);background:transparent;color:var(--text);
  font-weight:600;font-size:13px;cursor:pointer;text-align:left}
.bgb small{display:block;font-weight:400;color:var(--muted);margin-top:2px}
.bgb.sel{border-color:var(--accent);box-shadow:0 0 0 2px var(--accent) inset}
.cardtitle{font-weight:700;font-size:16px;margin:0 0 10px}
.schedline{margin-top:10px;font-size:13px;color:var(--accent)}
.schedline:empty{display:none}
.seg{display:grid;grid-template-columns:repeat(3,1fr);gap:8px}
.seg button{padding:10px 8px;border-radius:10px;border:1px solid var(--line);background:transparent;color:var(--text);
  cursor:pointer;text-align:center;font-weight:400}
.seg button b{display:block;font-size:14px}
.seg button small{display:block;font-size:11px;color:var(--muted);margin-top:2px}
.seg button.sel{border-color:var(--accent);box-shadow:0 0 0 2px var(--accent) inset}
.modebox{margin-top:12px}
.modebox p{margin:0 0 6px}
.lead{font-size:15px}
.ok{color:var(--go)}
.week{display:flex;flex-direction:column;gap:6px;margin-top:6px}
.day{display:flex;align-items:center;gap:10px;flex-wrap:wrap;padding:8px 10px;border:1px solid var(--line);border-radius:10px}
.day .check{min-width:120px;margin:0}
.day .times{display:flex;align-items:center;gap:6px;flex:1;min-width:220px}
.day input[type=time]{width:auto;flex:1;padding:6px 8px}
.day.off .times{opacity:.4}
.day .to{color:var(--muted);font-size:13px}
.day .bad{color:var(--stop);font-size:12px;width:100%}
.day .bad:empty{display:none}
details.inner{border-top:1px solid var(--line);margin-top:12px;padding-top:12px}
details.inner.flush{border:0;margin:0;padding:0}
details.inner>summary{font-weight:600}
.howto{margin:8px 0 0;padding-left:18px;font-size:14px}
.howto li{margin:6px 0}
.drillrow{display:flex;align-items:center;gap:10px;margin-top:12px;flex-wrap:wrap}
.switch{position:relative;display:inline-block;width:46px;height:26px;flex:none;margin:0}
.switch input{opacity:0;width:0;height:0}
.switch span{position:absolute;inset:0;border-radius:999px;background:var(--line);transition:.2s;cursor:pointer}
.switch span:before{content:"";position:absolute;width:20px;height:20px;left:3px;top:3px;border-radius:50%;background:#fff;transition:.2s}
.switch input:checked+span{background:var(--go)}
.switch input:checked+span:before{transform:translateX(20px)}
.clist{margin-top:8px}
/* ===== tabs ===== */
.tabs{position:sticky;top:8px;z-index:5;display:flex;gap:4px;padding:5px;margin:0 0 14px;background:var(--card);
  backdrop-filter:var(--card-blur);-webkit-backdrop-filter:var(--card-blur);border:1px solid var(--line);border-radius:14px;overflow-x:auto;scrollbar-width:none}
.tabs::-webkit-scrollbar{display:none}
.tabs button{flex:1 0 auto;background:transparent;color:var(--muted);padding:9px 12px;border-radius:10px;font-size:13px;
  white-space:nowrap;font-weight:600;transition:background .15s,color .15s}
.tabs button:hover{color:var(--text)}
.tabs button.on{background:color-mix(in srgb,var(--accent) 16%,transparent);color:var(--text);box-shadow:inset 0 0 0 1px color-mix(in srgb,var(--accent) 60%,transparent)}
section.tab{display:none}
section.tab.on{display:block;animation:tabin .18s ease-out}
body.alltabs section.tab{display:block}
@keyframes tabin{from{opacity:0;transform:translateY(4px)}to{opacity:1;transform:none}}
.storechips{display:flex;gap:6px;flex-wrap:wrap;margin-top:14px}
.storechips:empty{display:none}
.schip{display:inline-flex;align-items:center;gap:7px;font-size:12px;padding:6px 11px;border-radius:999px;border:1px solid var(--line);
  background:transparent;color:var(--text);font-weight:500;cursor:pointer}
.schip i{width:8px;height:8px;border-radius:50%;background:var(--muted);flex:none}
.schip.on i{background:var(--go)}.schip.checking i{background:var(--accent);animation:pulse 1.4s infinite}
.schip.error i{background:var(--stop)}.schip.paused i,.schip.waiting i{background:var(--found)}
.schip.off{color:var(--muted)}
.swgrid{display:grid;gap:2px}
.swrow{display:flex;align-items:center;justify-content:space-between;gap:12px;margin:0;padding:10px 2px;color:var(--text);font-size:15px;
  border-top:1px solid var(--line);cursor:pointer}
.swrow:first-child{border-top:0}
summary.cardtitle{margin:0;font-size:16px}
@media (max-width:480px){.tabs{gap:2px;padding:4px}.tabs button{padding:8px 8px;font-size:12px}}
.crow{display:block;padding:9px 0;border-top:1px solid var(--line);text-decoration:none;color:var(--text);cursor:pointer}
.crow:first-child{border-top:0}
.crow:hover .cname{color:var(--accent)}
.cname{font-size:14px;font-weight:600;line-height:1.3}
.ntag.st-in,.ntag.st-open{color:var(--go);border-color:color-mix(in srgb,var(--go) 45%,transparent)}
.ntag.st-upcoming{color:var(--accent);border-color:color-mix(in srgb,var(--accent) 45%,transparent)}
.cardhead{display:flex;align-items:center;justify-content:space-between;gap:10px}
button.small{padding:6px 12px;font-size:13px}
.chips{display:flex;gap:6px;flex-wrap:wrap;margin:12px 0 6px}
.chips button{padding:5px 11px;border-radius:999px;border:1px solid var(--line);background:transparent;color:var(--muted);
  font-size:12px;font-weight:600;cursor:pointer}
.chips button.sel{color:var(--text);border-color:var(--accent)}
.ngroup{font-size:11px;font-weight:700;letter-spacing:.1em;text-transform:uppercase;color:var(--muted);margin:14px 0 4px}
.news{display:flex;gap:12px;padding:10px 0;border-top:1px solid var(--line);text-decoration:none;color:var(--text)}
.news:first-of-type{border-top:0}
.news:hover .nt{color:var(--accent)}
.nd{flex:none;width:44px;text-align:center;border:1px solid var(--line);border-radius:8px;padding:4px 0;line-height:1.1}
.nd b{display:block;font-size:16px}
.nd small{font-size:10px;letter-spacing:.08em;color:var(--muted);text-transform:uppercase}
.nd.up{border-color:var(--go)}.nd.up small{color:var(--go)}
.nt{font-size:14px;font-weight:600;line-height:1.3}
.nm{font-size:11px;color:var(--muted);margin-top:4px}
.ntags{display:flex;gap:5px;flex-wrap:wrap;margin-top:5px}
.ntag{font-size:11px;font-weight:600;padding:2px 7px;border-radius:999px;border:1px solid var(--line);color:var(--muted)}
.ntag.k-preorders{color:var(--accent);border-color:color-mix(in srgb,var(--accent) 45%,transparent)}
.ntag.k-restock{color:var(--go);border-color:color-mix(in srgb,var(--go) 45%,transparent)}
.ntag.k-when{color:var(--found);border-color:color-mix(in srgb,var(--found) 45%,transparent)}
.ntag.cat-pkc{color:var(--found)}
.ntag.st-confirmed{color:var(--go);border-color:color-mix(in srgb,var(--go) 45%,transparent)}
.ntag.st-predicted{color:var(--found);border-color:color-mix(in srgb,var(--found) 45%,transparent)}
.nwhen{font-size:13px;color:var(--muted);margin-top:2px}
.nd.vague b{font-size:12px}
.hgrid{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-top:10px}
@media(max-width:560px){.hgrid{grid-template-columns:repeat(2,1fr)}}
.htile{border:1px solid var(--line);border-radius:10px;padding:8px 10px}
.htile small{display:block;font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.06em}
.htile b{font-size:18px}
.seg{display:flex;gap:6px;flex-wrap:wrap;margin-top:4px}
.seg button{padding:6px 12px;border-radius:999px;border:1px solid var(--line);background:transparent;color:var(--muted);font-weight:600;cursor:pointer}
.seg button.sel{color:var(--text);border-color:var(--accent)}
.cat{font-weight:600}
.cat-pkc{color:var(--found)}.cat-store{color:var(--go)}.cat-set{color:var(--accent)}
.sec{margin-top:16px;font-size:13px;font-weight:600;color:var(--muted);text-transform:uppercase;letter-spacing:.08em}
.wrap{position:relative;z-index:1}
:root:not([data-theme="classic"]) body::before{content:"";position:fixed;inset:0;z-index:0;pointer-events:none;
  background-image:linear-gradient(var(--line) 1px,transparent 1px),linear-gradient(90deg,var(--line) 1px,transparent 1px);
  background-size:44px 44px;opacity:.18;
  -webkit-mask-image:radial-gradient(ellipse at 50% 30%,#000 20%,transparent 75%);
  mask-image:radial-gradient(ellipse at 50% 30%,#000 20%,transparent 75%)}
:root:not([data-theme="classic"]) .card{backdrop-filter:var(--card-blur);-webkit-backdrop-filter:var(--card-blur);
  box-shadow:0 0 0 1px var(--line) inset,0 10px 30px rgba(0,0,0,.35)}
:root:not([data-theme="classic"]) h1{font-family:var(--head-font);letter-spacing:.14em;text-transform:uppercase;
  color:var(--accent);text-shadow:0 0 12px var(--glow),0 0 2px var(--accent)}
:root:not([data-theme="classic"]) .big{font-family:var(--head-font);letter-spacing:.02em}
:root:not([data-theme="classic"]) button.start,:root:not([data-theme="classic"]) button.stopb{color:var(--btn-text);
  text-transform:uppercase;letter-spacing:.08em;font-family:var(--head-font)}
:root:not([data-theme="classic"]) button.start:not(:disabled){box-shadow:0 0 18px color-mix(in srgb,var(--go) 55%,transparent)}
:root:not([data-theme="classic"]) button.stopb:not(:disabled){box-shadow:0 0 18px color-mix(in srgb,var(--stop) 55%,transparent)}
:root:not([data-theme="classic"]) .plain{border-color:var(--line)}
:root:not([data-theme="classic"]) .plain:hover{box-shadow:0 0 12px var(--glow)}
:root:not([data-theme="classic"]) .watching .dot{box-shadow:0 0 12px var(--go),0 0 24px var(--go)}
:root:not([data-theme="classic"]) .found .dot{box-shadow:0 0 14px var(--found),0 0 30px var(--found)}
:root:not([data-theme="classic"]) .error .dot{box-shadow:0 0 12px var(--stop)}
:root:not([data-theme="classic"]) .found.card{animation:alarm 1.2s ease-in-out infinite}
@keyframes alarm{50%{box-shadow:0 0 0 1px var(--found) inset,0 0 36px color-mix(in srgb,var(--found) 55%,transparent)}}
:root:not([data-theme="classic"]) input,:root:not([data-theme="classic"]) select,:root:not([data-theme="classic"]) textarea{background:rgba(0,0,0,.28)}
:root:not([data-theme="classic"]) input:focus,:root:not([data-theme="classic"]) select:focus,:root:not([data-theme="classic"]) textarea:focus{outline:none;border-color:var(--accent);box-shadow:0 0 0 3px var(--glow)}
.tag{display:inline-block;font:600 11px/1 ui-monospace,Menlo,monospace;letter-spacing:.12em;padding:5px 8px;border-radius:999px;
  border:1px solid var(--line);color:var(--muted);vertical-align:middle;margin-left:10px;text-transform:uppercase}
.watching .tag,.tag.on{color:var(--go);border-color:color-mix(in srgb,var(--go) 50%,transparent)}
.head{display:flex;align-items:center;justify-content:space-between;margin-bottom:16px}
.head h1{margin:0}
.swatches{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-top:10px}
.sw{display:flex;align-items:center;gap:8px;padding:10px;border-radius:10px;border:1px solid var(--line);background:transparent;
  color:var(--text);font-weight:600;font-size:13px;cursor:pointer;text-align:left}
.sw i{width:18px;height:18px;border-radius:50%;flex:none}
.sw.sel{border-color:var(--accent);box-shadow:0 0 0 2px var(--accent) inset}
@media (prefers-reduced-motion:reduce){*{animation:none!important}}
</style>
<script>/* apply the saved theme before anything draws, so there's no flash */
try{document.documentElement.dataset.theme=localStorage.getItem("qw-theme")||"neon";}catch(e){document.documentElement.dataset.theme="neon";}
</script></head><body><div id="wp"><img id="wpImg" alt=""><video id="wpVid" muted loop playsinline autoplay></video></div><canvas id="fx"></canvas><div class="wrap">
<div class="head"><h1>Guardian Monitor</h1><span class="tag" id="sysTag">Idle</span></div>

<!-- ===== 1. Status ===== -->
<div class="card" id="updBanner" style="display:none;border-color:var(--accent)">
  <div class="cardhead"><span class="cardtitle" style="margin:0" id="updTitle">Update ready</span>
  <button class="small" onclick="doUpdate()">Update now</button></div>
  <div class="sub" id="updNotes"></div>
</div>
<div class="card" id="statusCard">
  <div class="status"><div class="dot"></div>
    <div><div class="big" id="msg">Loading...</div><div class="sub" id="sub"></div></div></div>
  <div class="schedline" id="schedLine"></div>
  <div class="sub" id="healthLine" title="Guardian Monitor checks your computer's temperature, processor and memory every minute and eases off if it's working too hard."></div>
  <div class="btns">
    <button class="start" id="startBtn" onclick="act('start')">Start now</button>
    <button class="stopb" id="stopBtn" onclick="act('stop')">Stop</button>
  </div>
  <div class="drillrow"><button class="plain small" id="drillBtn" onclick="runDrill()">Run a queue drill</button>
    <span class="sub" id="drillMsg">Tests the whole alert flow with a fake queue. Alerts say TEST.</span></div>
  <div class="btns" id="ackRow" style="display:none">
    <button class="start" style="background:var(--found);color:#1b1d22" onclick="act('ack')">I'm on it (stop the reminders)</button>
  </div>
  <div class="warn" id="jsWarn"></div>
  <div class="storechips" id="storeChips"></div>
</div>

<nav class="tabs" id="tabs">
  <button data-go="pkc">Pok&eacute;mon Center</button>
  <button data-go="stores">Stores</button>
  <button data-go="alerts">Alerts</button>
  <button data-go="drops">Releases</button>
  <button data-go="system">System</button>
</nav>
<section class="tab" data-tab="pkc">
<!-- ===== 2. When to watch ===== -->
<div class="card">
  <div class="cardtitle">When to watch</div>
  <div class="seg" id="modeSeg">
    <button data-mode="smart"><b>Drop hours</b><small>Recommended</small></button>
    <button data-mode="custom"><b>My schedule</b><small>Pick days &amp; times</small></button>
    <button data-mode="manual"><b>Manual</b><small>Only when I press Start</small></button>
  </div>

  <div class="modebox" id="box-smart">
    <p class="lead">Watches by itself <b>Monday&ndash;Friday, 9:45 AM &ndash; 2:30 PM</b> (Eastern).</p>
    <p class="sub">Past Pok&eacute;mon Center queues opened on weekdays between about 10 AM and 2 PM Eastern, mostly 11 AM&ndash;1 PM, and most often Monday&ndash;Wednesday. None have been logged on weekends. That's about 330 refreshes a day instead of 2,000 if it ran nonstop.</p>
  </div>

  <div class="modebox" id="box-custom">
    <p class="sub">Tick the days to watch and set the times (Eastern). Changes save automatically.</p>
    <div class="week" id="week"></div>
    <div class="btns">
      <button class="plain" onclick="copyMonday()">Copy Monday to all weekdays</button>
      <button class="plain" onclick="useDropHours()">Fill in drop hours</button>
    </div>
  </div>

  <div class="modebox" id="box-manual">
    <p class="sub">Nothing runs on its own. Press <b>Start now</b> whenever you want to watch, and <b>Stop</b> when you're done.</p>
  </div>
  <div class="sub ok" id="schedMsg"></div>

  <details class="inner">
    <summary>How the schedule works (what needs to be on)</summary>
    <ul class="howto">
      <li><b>Your PC stays on.</b> While a schedule is set or it's watching, the app keeps your PC from sleeping (the screen can still turn off). It can't wake a PC that's asleep or shut down.</li>
      <li><b>Guardian Monitor stays open.</b> You can close this browser tab; the app keeps running. To open it automatically when your PC starts, run <b>Setup</b> again and answer <b>y</b> to the start-up question (or press <b>Win+R</b>, type <b>shell:startup</b>, and copy the Guardian Monitor shortcut there).</li>
      <li><b>You're signed in to the site in Chrome.</b> The app uses its own Chrome window (separate from your normal Chrome). Sign in to the site in that window once, or save your login below, and it stays signed in.</li>
      <li><b>You can always press Start now or Stop.</b> If you press Stop, a queue is found, or a block page shows up, it waits until the next scheduled time, so it never refreshes you out of a queue.</li>
    </ul>
  </details>
</div>

<!-- ===== 3. What to watch ===== -->
<div class="card">
  <label for="watch_url" class="cardtitle" style="display:block;color:var(--text);font-size:16px;margin:0 0 10px">Website to watch</label>
  <div style="display:flex;gap:8px">
    <input id="watch_url" placeholder="https://www.pokemoncenter.com/" onkeydown="if(event.key==='Enter')saveUrl()">
    <button class="plain" id="urlBtn" onclick="saveUrl()" style="flex:none">Save</button>
  </div>
  <div class="sub" id="urlMsg"></div>

  <details class="inner" id="loginBox">
    <summary>Stay signed in</summary>
    <label class="check"><input id="check_signed_in" type="checkbox"> When a run starts, check that I'm signed in (pings me if not)</label>
    <label class="check"><input id="auto_login" type="checkbox"> Sign me in automatically if I'm signed out</label>
    <label>Sign-in page address (open the site's sign-in page in Chrome and copy the address bar)</label>
    <input id="login_url" placeholder="https://www.example.com/sign-in">
    <label>Email or username</label>
    <input id="login_user_in" autocomplete="off" spellcheck="false">
    <label>Password</label>
    <input id="login_pw" type="password" autocomplete="new-password">
    <div class="btns">
      <button class="plain" onclick="saveLogin()">Save login</button>
      <button class="plain" id="forgetBtn" onclick="forgetLogin()">Forget login</button>
    </div>
    <div class="sub" id="loginMsg"></div>
    <p class="sub" style="margin-top:10px">Your password is stored only in Windows Credential Manager, never in the app's files, and is only ever typed into this site's own sign-in page. If the site shows a captcha ("I'm not a robot"), it fills in your login, brings that Chrome window to the front, and pings you. You solve the captcha and press Sign in, and it goes back to watching.</p>
  </details>
</div>

<div class="card">
  <details class="inner flush" id="settingsBox"><summary>Watch settings</summary>
    <div class="row">
      <div><label>Refresh every (min sec)</label><input id="min_wait" type="number" min="15"></div>
      <div><label>to (max sec)</label><input id="max_wait" type="number" min="15"></div>
    </div>
    <label class="check"><input type="checkbox" id="pkc_background"> Keep the Pok&eacute;mon Center window minimized while watching. It pops up with your spot when a queue hits.</label>
    <label>Queue words: any of these on the page = queue (comma separated)</label><input id="queue_words">
    <label>Block words: any of these = blocked/error page, so it pauses and pings you</label><input id="block_words">
    <label>Heads-up words: site is down/overloaded, so it pings once and keeps watching</label><input id="headsup_words">
    <label class="check"><input id="use_script_check" type="checkbox">
      Also detect the queue by its waiting-room script (turn off if you get false alarms)</label>
    <label>Signed-in words: any of these = you're signed in</label><input id="signed_in_words">
    <label>Signed-out words: any of these (and no signed-in words) = you're signed out</label><input id="signed_out_words">
    <div class="btns"><button class="plain" onclick="save()">Save</button><span class="saved" id="saved"></span></div>
  </details>
</div>
</section>

<section class="tab" data-tab="stores">
<div class="card storebar">
  <div class="cardtitle">Store checkers</div>
  <div class="sub" style="margin:-6px 0 10px">Each one runs on its own, in the background. Turn on only the ones this computer should handle, so Discord doesn't get the same alert twice.</div>
  <div class="swgrid">
    <label class="swrow"><span>Target restocks</span><span class="switch"><input type="checkbox" data-mirror="target_enabled"><span></span></span></label>
    <label class="swrow"><span>Best Buy restocks &amp; invites</span><span class="switch"><input type="checkbox" data-mirror="bestbuy_enabled"><span></span></span></label>
    <label class="swrow"><span>Walmart restocks &amp; drawings</span><span class="switch"><input type="checkbox" data-mirror="walmart_enabled"><span></span></span></label>
  </div>
</div>
<!-- ===== Target restocks ===== -->
<div class="card" id="targetCard">
  <div class="cardhead">
    <span class="cardtitle" style="margin:0">Target restocks</span>
    <label class="switch" title="Turn the Target checker on or off"><input type="checkbox" id="target_enabled"><span></span></label>
  </div>
  <div class="sub" id="targetStatus">Off</div>
  <div class="clist" id="targetList"></div>
  <div class="btns">
    <button class="plain small" id="targetNow" onclick="checkNow('target')">Check now</button>
    <button class="plain small" onclick="targetTest()">Send test alert</button>
  </div>
  <div class="sub" id="targetTestMsg"></div>
  <details class="inner"><summary>Target settings</summary>
    <label>Target product links to watch, one per line (up to 10)</label>
    <textarea id="target_watch" rows="4" spellcheck="false" placeholder="https://www.target.com/p/pokemon-trading-card-game-.../-/A-12345678"></textarea>
    <label class="check"><input type="checkbox" id="target_scan"> Also scan Target's Pok&eacute;mon card search for anything in stock</label>
    <label class="check"><input type="checkbox" id="target_open_page"> Open the product page in Chrome when it's in stock</label>
    <label>When to check</label>
    <select id="target_hours">
      <option value="always">All the time</option>
      <option value="overnight">Overnight only, 12:30&ndash;4 AM (when Target usually restocks online)</option>
    </select>
    <label>Check every (minutes, 2 or more)</label><input id="target_every" type="number" min="2">
    <label>Who to @ for Target restocks</label>
    <select id="target_mention">
      <option value="same">The Target role (set in Discord settings)</option>
      <option value="none">Nobody</option>
      <option value="here">@here</option>
      <option value="everyone">@everyone</option>
    </select>
    <label>Discord message (use {item}, {price} and {link}; the @ goes in front automatically)</label>
    <textarea id="discord_target_msg" rows="3"></textarea>
    <p class="sub" style="margin-top:8px">Alerts only for official products sold by Target, at Target's retail price or up to $10 over (cheap items get less wiggle room). You can change this in Advanced. Third-party sellers, imports, repacks and products with no MSRP limit are skipped, and search hits are double-checked on their own page. It never buys anything, and it checks invisibly in the background. The only page it ever opens is one that just restocked.</p>
    <div class="btns"><button class="plain" onclick="save()">Save</button></div>
  </details>
</div>

<!-- ===== Best Buy restocks ===== -->
<div class="card" id="bestbuyCard">
  <div class="cardhead">
    <span class="cardtitle" style="margin:0">Best Buy restocks</span>
    <label class="switch" title="Turn the Best Buy checker on or off"><input type="checkbox" id="bestbuy_enabled"><span></span></label>
  </div>
  <div class="sub" id="bestbuyStatus">Off</div>
  <div class="clist" id="bestbuyList"></div>
  <div class="btns">
    <button class="plain small" id="bestbuyNow" onclick="checkNow('bestbuy')">Check now</button>
    <button class="plain small" onclick="bestbuyTest()">Send test alert</button>
  </div>
  <div class="sub" id="bestbuyTestMsg"></div>
  <details class="inner"><summary>Best Buy settings</summary>
    <label>Best Buy product links to watch, one per line (up to 10)</label>
    <textarea id="bestbuy_watch" rows="4" spellcheck="false" placeholder="https://www.bestbuy.com/site/pokemon-trading-card-game-.../1234567.p?skuId=1234567"></textarea>
    <label class="check"><input type="checkbox" id="bestbuy_scan"> Also scan Best Buy's Pok&eacute;mon card search for anything in stock</label>
    <label class="check"><input type="checkbox" id="bestbuy_open_page"> Open the product page in Chrome when it's in stock</label>
    <label>Check every (minutes, 3 or more)</label><input id="bestbuy_every" type="number" min="3">
    <label>Who to @ for Best Buy restocks</label>
    <select id="bestbuy_mention">
      <option value="same">The Best Buy role (set in Discord settings)</option>
      <option value="none">Nobody</option>
      <option value="here">@here</option>
      <option value="everyone">@everyone</option>
    </select>
    <label>Discord message (use {item}, {price} and {link}; the @ goes in front automatically)</label>
    <textarea id="discord_bestbuy_msg" rows="3"></textarea>
    <p class="sub" style="margin-top:8px">Alerts only for official Pok&eacute;mon card products (no merch) sold by Best Buy itself, at retail price or a little over (your MSRP limits in Advanced). Marketplace sellers, imports and repacks are skipped. A drop's "Please Wait" queue button counts as in stock. When Best Buy opens <b>invite requests</b> (their drawing: you request an invite, and people picked get 24 hours to buy), you get a ping once per product so you can enter. It never buys anything or enters for you.</p>
    <div class="btns"><button class="plain" onclick="save()">Save</button></div>
  </details>
</div>

<!-- ===== Walmart drawings ===== -->
<div class="card" id="walmartCard">
  <div class="cardhead">
    <span class="cardtitle" style="margin:0">Walmart restocks &amp; drawings</span>
    <label class="switch" title="Turn the Walmart checker on or off"><input type="checkbox" id="walmart_enabled"><span></span></label>
  </div>
  <div class="sub" id="walmartStatus">Off</div>
  <div class="clist" id="walmartList"></div>
  <div class="btns">
    <button class="plain small" id="walmartNow" onclick="checkNow('walmart')">Check now</button>
    <button class="plain small" onclick="shopTest('walmart')">Send test alert</button>
    <button class="plain small" onclick="api('/open_url',{url:'https://www.walmart.com/shop/collectibles/draw'})">Open drawings page</button>
  </div>
  <div class="sub" id="walmartTestMsg"></div>
  <details class="inner"><summary>Walmart settings</summary>
    <label>Check every (minutes, 10 or more)</label><input id="walmart_every" type="number" min="10">
    <p class="sub">Drawings alert for every Pok&eacute;mon card product, whatever the price: once when it's coming up, again when it opens.</p>
    <label class="check"><input type="checkbox" id="walmart_products"> Also check Walmart's Pok&eacute;mon card products for MSRP restocks (sold by Walmart only)</label>
    <label>Walmart product links to watch, one per line (up to 10)</label>
    <textarea id="walmart_watch" rows="3" spellcheck="false" placeholder="https://www.walmart.com/ip/.../123456789"></textarea>
    <label class="check"><input type="checkbox" id="walmart_open_page"> Open the product page in Chrome when it's in stock</label>
    <label>Who to @ for Walmart restocks</label>
    <select id="walmart_mention">
      <option value="same">The Walmart role (set in Discord settings)</option>
      <option value="none">Nobody</option>
      <option value="here">@here</option>
      <option value="everyone">@everyone</option>
    </select>
    <label>Discord message for restocks (use {item}, {price} and {link})</label>
    <textarea id="discord_walmart_msg" rows="3"></textarea>
    <p class="sub" style="margin-top:8px">Watches Walmart's collectibles drawing page and pings you when a Pok&eacute;mon drawing opens or is announced (you still enter yourself). It also checks Walmart's Pok&eacute;mon card search for products <b>sold by Walmart</b> at MSRP (no merch, no Marketplace resellers).</p>
    <div class="btns"><button class="plain" onclick="save()">Save</button></div>
  </details>
</div>

<div class="card">
  <details class="inner flush"><summary>Price limits (MSRP) &amp; how stores are read</summary>
    <label>Alert up to this many dollars over retail ($). Cheap items get less: never more than 25% over.</label><input id="msrp_allowance" type="number" min="0" step="1">
    <p class="sub">Each store has its own list, because they price things differently. Format: <code>Product | price</code>, one per line, most specific first (the first match wins). Bundles count each item, so a 2-pack is 2x. Every limit also gets a small cushion so a few cents of rounding never blocks an alert.</p>
    <label>Target (Target's own shelf prices)</label>
    <textarea id="msrp_limits" rows="6" spellcheck="false"></textarea>
    <label>Best Buy</label>
    <textarea id="msrp_bestbuy" rows="6" spellcheck="false"></textarea>
    <label>Walmart</label>
    <textarea id="msrp_walmart" rows="6" spellcheck="false"></textarea>
    <label class="check"><input type="checkbox" id="checker_invisible"> Read store pages invisibly (no windows or tabs). Recommended.</label>
    <div class="btns"><button class="plain" onclick="save()">Save</button></div>
  </details>
</div>
</section>

<section class="tab" data-tab="alerts">
<div class="card">
  <details class="inner flush" id="phoneBox" open>
    <summary class="cardtitle">Phone (ntfy app)</summary>
    <ol>
      <li>Get the free <b>ntfy</b> app on your phone.</li>
      <li>Tap <b>+</b> and subscribe to: <span class="topic" id="topicShow"></span></li>
      <li>Press <b>Send test ping</b>.</li>
    </ol>
    <div class="btns"><button class="plain" onclick="act('test')">Send test ping</button></div>
    <h4 style="margin:16px 0 4px">Control it from your phone</h4>
    <label class="check"><input type="checkbox" id="phone_control"> Let my phone Start, Stop and check Status</label>
    <p class="sub">Every reply comes with <b>Start</b>, <b>Stop</b> and <b>Status</b> buttons. To get them any time, save these
      links as bookmarks or home-screen shortcuts on your phone (tap one and Guardian Monitor answers in ntfy within a few seconds):</p>
    <div class="sub" id="phoneLinks"></div>
    <div class="btns"><button class="plain" onclick="sendControls()">Send the buttons to my phone</button>
      <span class="saved" id="phoneMsg"></span></div>
    <label>Your ntfy topic (your phone must subscribe to this exact name)</label><input id="ntfy_topic">
    <div class="btns"><button class="plain" onclick="save()">Save</button></div>
  </details>
</div>
<div class="card">
  <details class="inner flush" id="discordBox" open>
    <summary class="cardtitle">Discord</summary>
    <label class="check"><input id="discord_enabled" type="checkbox"> Send alerts to Discord</label>
    <label>Pok&eacute;mon Center queue channel webhook (channel settings &rarr; Integrations &rarr; Webhooks)</label>
    <input id="discord_webhook" placeholder="https://discord.com/api/webhooks/...">
    <div class="sub">Pok&eacute;mon Center queue alerts post here. Target, Best Buy and Walmart post here too if you leave their boxes blank.</div>
    <label>Target restocks channel webhook (optional)</label>
    <input id="discord_webhook_target" placeholder="Blank = same as above">
    <label>Target role ID to @ (blank = same as the queue alert)</label>
    <input id="discord_role_target" placeholder="1561324538686611467">
    <label>Walmart drawings channel webhook (optional)</label>
    <input id="discord_webhook_walmart" placeholder="Blank = same as above">
    <label>Walmart role ID to @ when a drawing opens (blank = same as the queue alert)</label>
    <input id="discord_role_walmart" placeholder="1561324931176734760">
    <label>Best Buy restocks channel webhook (optional)</label>
    <input id="discord_webhook_bestbuy" placeholder="Blank = same as the top one">
    <label>Best Buy role ID to @ (blank = same as the queue alert)</label>
    <input id="discord_role_bestbuy" placeholder="Paste the role ID">
    <label>Also @ this role on <b>every</b> real alert (your Pok&eacute;mon role; blank = off)</label>
    <input id="discord_role_all" placeholder="1558918973842661506">

    <label>Bot name</label>
    <input id="discord_name" maxlength="80" placeholder="PKC Alerts">
    <label>Bot picture</label>
    <div style="display:flex;gap:12px;align-items:center">
      <img id="pfpPreview" alt="" style="width:56px;height:56px;border-radius:50%;object-fit:cover;background:var(--bg);border:1px solid var(--line);flex:none">
      <button class="plain" id="pfpBtn" onclick="pickPicture()">Choose picture...</button>
    </div>
    <div class="btns"><button class="plain" onclick="applyProfile()">Update name &amp; picture in Discord</button></div>
    <div class="sub" id="profileMsg"></div>

    <label>Who to @ when the queue goes live</label>
    <select id="discord_mention">
      <option value="none">Nobody (just post the alert)</option>
      <option value="here">@here (people online)</option>
      <option value="everyone">@everyone</option>
      <option value="role">A role (paste the role ID below)</option>
      <option value="user">Just me (paste your user ID below)</option>
    </select>
    <label>Role or user ID (right-click the role or your name &rarr; Copy ID, needs Developer Mode)</label>
    <input id="discord_user_id" placeholder="123456789012345678">

    <label>Queue alert message (use {link} for the link; the @ goes in front automatically)</label>
    <textarea id="discord_queue_msg" rows="3"></textarea>
    <label>Heads-up message (no @, since it's only a maybe)</label>
    <textarea id="discord_headsup_msg" rows="3"></textarea>
    <div class="btns">
      <button class="plain" onclick="save()">Save Discord settings</button>
      <button class="plain" onclick="discordTest()">Send preview to Discord</button>
    </div>
    <div class="sub" id="discordMsg"></div>
  </details>
</div>
</section>

<section class="tab" data-tab="drops">
<!-- ===== Releases & drops ===== -->
<div class="card" id="newsCard">
  <div class="cardhead">
    <span class="cardtitle" style="margin:0">Releases &amp; drops</span>
    <button class="plain small" id="newsBtn" onclick="refreshNews()">Refresh</button>
  </div>
  <div class="sub" id="newsUpdated">Loading...</div>
  <div class="chips" id="newsChips">
    <button data-c="all" class="sel">All</button>
    <button data-c="confirmed">Confirmed</button>
    <button data-c="predicted">Predicted</button>
    <button data-c="pkc">Pok&eacute;mon Center</button>
    <button data-c="store">In stores</button>
  </div>
  <div id="newsList"></div>
  <button class="plain small" id="newsMore" style="display:none;margin-top:8px" onclick="newsLimit+=10;renderNews()">Show more</button>
  <div class="sub" id="newsErr"></div>
  <details class="inner"><summary>Sources</summary>
    <label>One per line, <code>Name | link</code>. A news feed (RSS/Atom) or a release-calendar web page both work.</label>
    <textarea id="news_feeds" rows="4" spellcheck="false"></textarea>
    <div class="btns"><button class="plain" onclick="save()">Save</button></div>
  </details>
</div>

</section>

<section class="tab" data-tab="system">
<!-- ===== Computer health ===== -->
<div class="card" id="healthCard">
  <div class="cardhead"><span class="cardtitle" style="margin:0">Computer health</span>
    <span class="sub" id="hcChecked"></span></div>
  <div class="hgrid">
    <div class="htile"><small>Temperature</small><b id="hcTemp">–</b></div>
    <div class="htile"><small>Processor</small><b id="hcCpu">–</b></div>
    <div class="htile"><small>Memory free</small><b id="hcMem">–</b></div>
    <div class="htile"><small>Stuck Chrome closed</small><b id="hcClean">0</b></div>
  </div>
  <div class="sub" id="hcStatus" style="margin-top:8px"></div>
  <label style="margin-top:12px">Safety</label>
  <div class="seg" id="hcMode">
    <button data-m="protect">Protect</button><button data-m="warn">Warn only</button><button data-m="off">Off</button>
  </div>
  <div class="sub" id="hcModeHelp"></div>
</div>

<!-- ===== 5. Look ===== -->
<div class="card">
  <details class="inner flush"><summary>Appearance</summary>
    <div class="swatches" id="swatches">
      <button class="sw" data-t="neon"><i style="background:linear-gradient(135deg,#00e5ff,#ff2e88)"></i>Neon</button>
      <button class="sw" data-t="matrix"><i style="background:linear-gradient(135deg,#39ff14,#014d0a)"></i>Matrix</button>
      <button class="sw" data-t="synth"><i style="background:linear-gradient(135deg,#ff4ecd,#00f0ff)"></i>Synthwave</button>
      <button class="sw" data-t="ice"><i style="background:linear-gradient(135deg,#bae6fd,#2563eb)"></i>Ice</button>
      <button class="sw" data-t="ember"><i style="background:linear-gradient(135deg,#ffb703,#ff3d3d)"></i>Ember</button>
      <button class="sw" data-t="classic"><i style="background:linear-gradient(135deg,#f4f5f7,#2f6fde)"></i>Classic</button>
    </div>
    <div class="sec">Moving background</div>
    <div class="bgs">
      <button class="bgb" data-bg="particles">Particles<small>Glowing connected dots</small></button>
      <button class="bgb" data-bg="blackhole">Black Hole<small>Spinning glowing disk</small></button>
      <button class="bgb" data-bg="peaks">Northern Peaks<small>Snowy mountains, aurora</small></button>
      <button class="bgb" data-bg="arcane">Arcane Storm<small>Magic circle, embers, lightning</small></button>
      <button class="bgb" data-bg="warp">Warp Speed<small>Stars rushing past</small></button>
      <button class="bgb" data-bg="nebula">Nebula<small>Drifting space clouds</small></button>
      <button class="bgb" data-bg="custom">Your wallpaper<small>Your own picture or video</small></button>
      <button class="bgb" data-bg="off">Off<small>Plain background</small></button>
    </div>
    <div id="wpRow" style="display:none">
      <label>A picture or video from your PC (videos loop silently)</label>
      <button class="plain" id="wpBtn" onclick="pickWallpaper()">Choose picture or video...</button>
      <div class="btns"><button class="plain" onclick="clearWallpaper()">Remove wallpaper</button></div>
      <div class="sub" id="wpMsg"></div>
    </div>
    <div class="sub" style="margin-top:10px">Backgrounds match your color theme and pause when this tab is hidden, so they don't slow your PC.</div>
  </details>
</div>

<!-- ===== 7. Log ===== -->
<div class="card">
  <details class="inner flush"><summary>Activity log</summary><div class="log" id="log"></div></details>
  <div class="btns"><button class="plain" id="updBtn" onclick="doUpdate()">Update</button>
  <button class="plain" id="quitBtn" onclick="quitApp()">Quit Guardian Monitor</button></div>
  <div class="sub" id="updMsg"></div>
</div>
</section>
</div>
<script>
const TOKEN="__TOKEN__";
const FIELDS=["discord_webhook","discord_webhook_target","discord_webhook_walmart","discord_role_target","discord_role_walmart","discord_webhook_bestbuy","discord_role_bestbuy","discord_role_all","msrp_bestbuy","msrp_walmart","walmart_watch","walmart_mention","discord_walmart_msg","bestbuy_watch","bestbuy_every","bestbuy_mention","discord_bestbuy_msg","discord_name","discord_queue_msg","discord_headsup_msg","discord_mention","discord_user_id","news_feeds","target_watch","target_hours","target_every","target_mention","discord_target_msg","walmart_every","msrp_limits","msrp_allowance","login_url","signed_in_words","signed_out_words","ntfy_topic","min_wait","max_wait","watch_url","queue_words","block_words","headsup_words"];
let filled=false;
async function api(path,body){
  const r=await fetch(path,{method:body===undefined?"GET":"POST",
    headers:{"X-Token":TOKEN,"Content-Type":"application/json"},
    body:body===undefined?undefined:JSON.stringify(body)});
  return r.json();
}
async function act(a){await api("/"+a,{});refresh();}
async function save(){
  const s={};FIELDS.forEach(f=>s[f]=document.getElementById(f).value);
  s.use_script_check=document.getElementById("use_script_check").checked;
  s.discord_enabled=document.getElementById("discord_enabled").checked;
  s.schedule_mode=schedMode;s.schedule_custom=week;
  s.check_signed_in=document.getElementById("check_signed_in").checked;
  ["target_enabled","target_scan","target_open_page","walmart_enabled","checker_invisible","pkc_background","phone_control","bestbuy_enabled","bestbuy_scan","bestbuy_open_page","walmart_products","walmart_open_page"].forEach(k=>s[k]=document.getElementById(k).checked);
  s.auto_login=document.getElementById("auto_login").checked;
  const r=await api("/save",s);fill(r.settings,true);
  ["saved","discordMsg"].forEach(id=>{const el=document.getElementById(id);el.textContent="Saved";
    setTimeout(()=>el.textContent="",2000);});
}
async function saveUrl(){
  const el=document.getElementById("urlMsg"),box=document.getElementById("watch_url");
  let v=box.value.trim();
  if(v&&!/^https?:\/\//i.test(v))v="https://"+v;   // "pokemoncenter.com" works too
  box.value=v;await save();
  const now=document.getElementById("watch_url").value;
  el.textContent=now===v?"Saved. Press Start to watch this site.":"That doesn't look like a web address, so it was reset.";
  setTimeout(()=>el.textContent="",4000);
}
function mask(u){const at=u.indexOf("@");return at>1?u[0]+"***"+u.slice(at):u.slice(0,2)+"***";}
function showLogin(s){
  document.getElementById("login_user_in").value=s.login_user||"";
  document.getElementById("loginMsg").textContent=s.login_saved&&s.login_user
    ?`Login saved in Windows Credential Manager (${mask(s.login_user)}).`:"No login saved.";
}
["check_signed_in","auto_login"].forEach(id=>document.getElementById(id).onchange=()=>{
  if(id==="auto_login"&&document.getElementById(id).checked&&!document.getElementById("login_url").value)
    document.getElementById("loginMsg").textContent="Add the sign-in page address and save your login too.";
  save();});
document.getElementById("login_url").onchange=()=>save();
async function saveLogin(){
  const u=document.getElementById("login_user_in").value.trim(),pw=document.getElementById("login_pw");
  const el=document.getElementById("loginMsg");el.textContent="Saving...";
  await save();
  const r=await api("/login_save",{user:u,password:pw.value});
  pw.value="";   // never keep the password on the page
  el.textContent=r.msg;if(r.ok){const st=await api("/status");fill(st.settings,true);el.textContent=r.msg;}
}
async function forgetLogin(){
  if(!confirmClick(document.getElementById("forgetBtn"),"Click again to remove"))return;
  const r=await api("/login_forget",{});const st=await api("/status");fill(st.settings,true);
  document.getElementById("loginMsg").textContent=r.msg;
}
async function discordTest(){
  await save();
  const r=await api("/test_discord",{});document.getElementById("discordMsg").textContent=r.msg;
}
let pfpData="";
async function pickPicture(){   // opens the normal file window
  const el=document.getElementById("profileMsg");el.textContent="Choose a picture in the window that opened...";
  const r=await api("/pick_picture",{});el.textContent=r.msg;
  if(r.ok){pfpData=r.data;document.getElementById("pfpPreview").src=pfpData;}
}
/* Two-click confirm that works inside the app window (no pop-up boxes needed). */
function confirmClick(btn,label){
  if(btn.dataset.armed){delete btn.dataset.armed;btn.textContent=btn.dataset.label;return true;}
  btn.dataset.label=btn.dataset.label||btn.textContent;btn.dataset.armed="1";btn.textContent=label;
  setTimeout(()=>{if(btn.dataset.armed){delete btn.dataset.armed;btn.textContent=btn.dataset.label;}},4000);
  return false;
}
async function applyProfile(){
  const el=document.getElementById("profileMsg");el.textContent="Updating...";
  await save();
  const r=await api("/discord_profile",{name:document.getElementById("discord_name").value,avatar:pfpData});
  el.textContent=r.msg;
}
function fill(s,force){
  if(filled&&!force)return;filled=true;
  FIELDS.forEach(f=>document.getElementById(f).value=s[f]);
  document.getElementById("use_script_check").checked=!!s.use_script_check;
  document.getElementById("discord_enabled").checked=!!s.discord_enabled;
  if(!force)fillSchedule(s);
  document.getElementById("check_signed_in").checked=!!s.check_signed_in;
  ["target_enabled","target_scan","target_open_page","walmart_enabled","checker_invisible","pkc_background","phone_control","bestbuy_enabled","bestbuy_scan","bestbuy_open_page","walmart_products","walmart_open_page"].forEach(k=>document.getElementById(k).checked=!!s[k]);
  showPhoneLinks(s);
  document.getElementById("auto_login").checked=!!s.auto_login;
  showLogin(s);
  document.getElementById("topicShow").textContent=s.ntfy_topic;
}

/* ===== Schedule controls ===== */
const DAYS=["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"];
const DROP_WEEK={};for(let d=0;d<7;d++)DROP_WEEK[d]={on:d<5,start:"09:45",end:"14:30"};
let schedMode="smart",week=JSON.parse(JSON.stringify(DROP_WEEK)),schedTimer=0;

function buildWeek(){
  const box=document.getElementById("week");box.innerHTML="";
  for(let d=0;d<7;d++){
    const w=week[d],row=document.createElement("div");row.className="day"+(w.on?"":" off");
    row.innerHTML=`<label class="check"><input type="checkbox" ${w.on?"checked":""}> ${DAYS[d]}</label>
      <span class="times"><input type="time" value="${w.start}" aria-label="${DAYS[d]} start">
      <span class="to">to</span><input type="time" value="${w.end}" aria-label="${DAYS[d]} end"></span>
      <span class="bad"></span>`;
    const [cb,st,en]=row.querySelectorAll("input");
    const upd=()=>{week[d]={on:cb.checked,start:st.value||"09:45",end:en.value||"14:30"};
      row.classList.toggle("off",!cb.checked);st.disabled=en.disabled=!cb.checked;
      const bad=cb.checked&&week[d].end<=week[d].start;
      row.querySelector(".bad").textContent=bad?"End must be after start":"";
      queueSaveSchedule();};
    st.disabled=en.disabled=!w.on;
    cb.onchange=upd;st.onchange=upd;en.onchange=upd;
    box.appendChild(row);
  }
}
function showMode(m){
  schedMode=m;
  document.querySelectorAll("#modeSeg button").forEach(b=>b.classList.toggle("sel",b.dataset.mode===m));
  ["smart","custom","manual"].forEach(x=>document.getElementById("box-"+x).style.display=x===m?"block":"none");
}
document.querySelectorAll("#modeSeg button").forEach(b=>b.onclick=()=>{showMode(b.dataset.mode);queueSaveSchedule(true);});
function queueSaveSchedule(now){
  clearTimeout(schedTimer);
  schedTimer=setTimeout(async()=>{await save();
    const el=document.getElementById("schedMsg");el.textContent="Saved";setTimeout(()=>el.textContent="",1800);
    refresh();},now?0:500);
}
function copyMonday(){for(let d=1;d<5;d++)week[d]={...week[0]};buildWeek();queueSaveSchedule(true);}
function useDropHours(){week=JSON.parse(JSON.stringify(DROP_WEEK));buildWeek();queueSaveSchedule(true);}
function fillSchedule(s){
  week={};for(let d=0;d<7;d++)week[d]={...(s.schedule_custom[d]||DROP_WEEK[d])};
  buildWeek();showMode(s.schedule_mode);
}
function scheduleText(d){
  const sc=d.schedule,running=d.status==="watching";
  if(!sc)return"";
  if(sc.mode==="manual")return running?"Manual run. Press Stop when you're done.":"Manual mode. Nothing runs until you press Start now.";
  const name=sc.mode==="smart"?"Drop hours":"My schedule";
  if(running&&sc.auto)return`${name}: watching until ${sc.window_end}.`;
  if(running)return`Started by you. ${name} won't stop this run.`;
  if(sc.held)return`${name}: paused for this window. Next: ${sc.next||"none set"}.`;
  if(sc.in_window)return`${name}: starting in a few seconds...`;
  return sc.next?`${name}: next run ${sc.next}.`:`${name}: no days turned on.`;
}

async function runDrill(){
  const r=await api("/drill_start",{});document.getElementById("drillMsg").textContent=
    r.ok?"Drill running: a test window opens, and in about 10 seconds you'll get TEST alerts.":r.msg;
  refresh();
}
async function quitApp(){
  if(!confirmClick(document.getElementById("quitBtn"),"Click again to quit (watching stops)"))return;
  try{await api("/quit",{});}catch(e){}
  document.body.innerHTML="<div class='wrap'><h1>Guardian Monitor is closed.</h1><p>Open the app again to start it.</p></div>";
}
async function refresh(){
  let d;try{d=await api("/status");}catch(e){
    document.getElementById("msg").textContent="Reconnecting... (if this stays, open Guardian Monitor again)";
    if(!window._reloading){window._reloading=setTimeout(()=>location.reload(),5000);}return;}
  fill(d.settings,false);
  const card=document.getElementById("statusCard");
  card.className="card "+d.status;
  FX.mode(d.status);
  renderCheckers(d.checkers);
  document.getElementById("schedLine").textContent=scheduleText(d);
  if(d.health){document.getElementById("healthLine").textContent=d.health.line+(d.health.level!=="ok"&&d.health.note?": "+d.health.note:"");
    renderHealth(d.health,d.settings.health_mode);}
  const tag=document.getElementById("sysTag");
  tag.textContent={watching:"Scanning",found:"Queue live",error:"Attention",idle:"Idle"}[d.status]||d.status;
  tag.className="tag"+(d.status==="watching"?" on":"");
  document.getElementById("msg").textContent=d.message;
  document.getElementById("sub").textContent=d.status==="watching"
    ?`Checks: ${d.checks}${d.last_check?" · last at "+d.last_check:""}`:"";
  const busy=d.status==="watching";
  document.getElementById("startBtn").disabled=busy;
  document.getElementById("stopBtn").disabled=!busy;
  document.getElementById("watch_url").disabled=busy;document.getElementById("urlBtn").disabled=busy;
  document.getElementById("drillBtn").disabled=busy;
  document.getElementById("jsWarn").style.display=d.js_warning?"block":"none";
  document.getElementById("jsWarn").textContent=(d.js_help||"")+" It can still spot a queue from the page address and title meanwhile.";
  document.getElementById("ackRow").style.display=(d.status==="found"&&d.alerting)?"flex":"none";
  document.getElementById("log").textContent=d.log.slice().reverse().join("\n");
  if(d.status==="found")document.title="🚨 QUEUE UP - Guardian Monitor";else document.title="Guardian Monitor";
}

/* ===== Theme + animated backgrounds ===== */
function store(k,v){try{localStorage.setItem(k,v);}catch(e){}}
function stored(k){try{return localStorage.getItem(k);}catch(e){return null;}}
function setTheme(t){
  document.documentElement.dataset.theme=t;store("qw-theme",t);
  document.querySelectorAll(".sw").forEach(b=>b.classList.toggle("sel",b.dataset.t===t));
  FX.color();
}
document.querySelectorAll(".sw").forEach(b=>b.onclick=()=>setTheme(b.dataset.t));

const FX=(()=>{
  const cv=document.getElementById("fx"),cx=cv.getContext("2d");
  let W=0,H=0,rgb=[0,229,255],acc=[0,229,255],fnd=[255,214,10];
  let mode="idle",scene="particles",raf=0,mouse={x:-9999,y:-9999},sparks=[],S={};
  const rnd=(a,b)=>a+Math.random()*(b-a);
  const rgba=(c,a)=>`rgba(${c[0]},${c[1]},${c[2]},${a})`;
  const mix=(a,b,k)=>a.map((v,i)=>Math.round(v+(b[i]-v)*k));
  const TAU=Math.PI*2;
  function hex(c,fb){c=(c||"").trim();if(c[0]!=="#")return fb;
    if(c.length===4)c="#"+c[1]+c[1]+c[2]+c[2]+c[3]+c[3];
    return[1,3,5].map(i=>parseInt(c.slice(i,i+2),16));}
  function stars(n){return Array.from({length:n},()=>({x:Math.random(),y:Math.random(),r:rnd(.4,1.4),p:rnd(0,TAU)}));}
  function drawStars(list,t,maxY){for(const s of list){if(maxY&&s.y>maxY)continue;
    cx.fillStyle=`rgba(255,255,255,${.25+.35*Math.sin(t*1.4+s.p)+.2})`;cx.fillRect(s.x*W,s.y*H,s.r,s.r);}}

  const SCENES={
    off:{still:true},
    custom:{still:true},

    /* Connected glowing dots */
    particles:{
      init(){S.pts=[];const n=Math.min(110,Math.round(W*H/11000));
        for(let i=0;i<n;i++)S.pts.push({x:rnd(0,W),y:rnd(0,H),vx:rnd(-.18,.18),vy:rnd(-.18,.18),r:rnd(.9,2.7)});},
      draw(t,sp){
        for(const p of S.pts){p.x+=p.vx*sp;p.y+=p.vy*sp;
          if(p.x<0||p.x>W)p.vx*=-1;if(p.y<0||p.y>H)p.vy*=-1;
          const dx=p.x-mouse.x,dy=p.y-mouse.y,d=Math.hypot(dx,dy);if(d<110&&d>0){p.x+=dx/d*1.2;p.y+=dy/d*1.2;}}
        cx.lineWidth=1;
        for(let i=0;i<S.pts.length;i++)for(let j=i+1;j<S.pts.length;j++){
          const a=S.pts[i],c=S.pts[j],d=Math.hypot(a.x-c.x,a.y-c.y);
          if(d<130){cx.strokeStyle=rgba(rgb,(1-d/130)*.5);cx.beginPath();cx.moveTo(a.x,a.y);cx.lineTo(c.x,c.y);cx.stroke();}}
        cx.shadowColor=rgba(rgb,1);cx.shadowBlur=8;cx.fillStyle=rgba(rgb,.9);
        for(const p of S.pts){cx.beginPath();cx.arc(p.x,p.y,p.r,0,TAU);cx.fill();}
        cx.shadowBlur=0;}},

    /* Spinning accretion disk around a black hole */
    blackhole:{
      init(){const wide=W>1050;S.R=Math.min(W,H)*(wide?.11:.085);S.cx=wide?W*.8:W/2;S.cy=wide?H*.48:H*.38;
        if(wide&&S.cx+S.R*5>W)S.cx=W-S.R*4.2;S.stars=stars(200);
        S.p=Array.from({length:1100},()=>this.spawn(true));},
      spawn(any){const R=S.R,q=Math.random();   // most matter crowds near the hole
        return{r:any?R*(1.2+4.3*q*q):rnd(R*4.4,R*5.6),a:rnd(0,TAU),s:rnd(.6,1.7)};},
      draw(t,sp){
        const R=S.R,tilt=.17,hot=[255,244,222];
        const g=cx.createRadialGradient(S.cx,S.cy,0,S.cx,S.cy,Math.max(W,H)*.75);
        g.addColorStop(0,rgba(acc,.13));g.addColorStop(1,"rgba(0,0,0,0)");cx.fillStyle=g;cx.fillRect(0,0,W,H);
        drawStars(S.stars,t);
        for(let i=0;i<S.p.length;i++){const p=S.p[i];
          p.a+=sp*.065*Math.pow(R/p.r,1.5);p.r-=sp*(.02+.35*R/p.r)*p.s*.25;
          if(p.r<R*1.1)S.p[i]=this.spawn(false);}
        const half=back=>{cx.globalCompositeOperation="lighter";
          for(const p of S.p){const sn=Math.sin(p.a);if((sn<0)!==back)continue;
            const k=Math.min(1,(p.r-R)/(R*4.2));
            cx.fillStyle=rgba(mix(hot,rgb,k),(1-k)*.85+.2);
            cx.fillRect(S.cx+Math.cos(p.a)*p.r,S.cy+sn*p.r*tilt,p.s*1.9,p.s*1.9);}
          cx.globalCompositeOperation="source-over";};
        half(true);
        cx.globalCompositeOperation="lighter";            // light bent around the hole
        const lg=cx.createRadialGradient(S.cx,S.cy,R*.95,S.cx,S.cy,R*2.1);
        lg.addColorStop(0,rgba(mix(hot,rgb,.25),.6));lg.addColorStop(.3,rgba(rgb,.2));lg.addColorStop(1,"rgba(0,0,0,0)");
        cx.fillStyle=lg;cx.beginPath();cx.arc(S.cx,S.cy,R*2.1,0,TAU);cx.fill();
        cx.globalCompositeOperation="source-over";
        cx.fillStyle="#000";cx.beginPath();cx.arc(S.cx,S.cy,R,0,TAU);cx.fill();   // event horizon
        cx.globalCompositeOperation="lighter";            // the far side of the disk, bent up over the top
        for(let j=0;j<3;j++){cx.strokeStyle=rgba(mix(hot,rgb,.15+j*.3),.55-j*.15);cx.lineWidth=3-j;
          cx.shadowColor=rgba(rgb,1);cx.shadowBlur=16;cx.beginPath();
          cx.ellipse(S.cx,S.cy,R*(1.18+j*.12),R*(1.1+j*.1),0,Math.PI*1.04,Math.PI*1.96);cx.stroke();}
        cx.shadowBlur=0;cx.globalCompositeOperation="source-over";
        cx.strokeStyle=rgba(mix(hot,rgb,.2),.9);cx.lineWidth=1.6;cx.shadowColor=rgba(rgb,1);cx.shadowBlur=20;
        cx.stroke();cx.shadowBlur=0;
        half(false);}},

    /* Snowy mountains under northern lights */
    peaks:{
      init(){S.stars=stars(150);S.layers=[0,1,2].map(i=>this.ridge(i));
        S.snow=Array.from({length:Math.round(W*H/8000)},()=>({x:rnd(0,W),y:rnd(0,H),r:rnd(.6,2.3),v:rnd(.3,1.1),d:rnd(0,TAU)}));},
      ridge(i){const n=257,v=new Array(n).fill(0);let rough=[.7,.55,.4][i];
        v[0]=rnd(.15,.6);v[n-1]=rnd(.15,.6);
        for(let step=n-1;step>1;step>>=1){for(let s=0;s+step<n;s+=step){const m=s+step/2;
          v[m]=(v[s]+v[s+step])/2+rnd(-1,1)*rough;}rough*=.56;}
        const base=[.6,.7,.82][i],amp=[.34,.24,.16][i];return v.map(y=>H*(base-Math.max(-.2,y)*amp));},
      draw(t,sp){
        const sky=cx.createLinearGradient(0,0,0,H);sky.addColorStop(0,"#01030a");
        sky.addColorStop(1,rgba(mix([3,6,14],acc,.2),1));cx.fillStyle=sky;cx.fillRect(0,0,W,H);
        drawStars(S.stars,t,.55);
        cx.globalCompositeOperation="lighter";
        const aur=[mix([60,255,170],rgb,.35),mix([60,255,170],acc,.6),mix([170,90,255],acc,.4)];
        for(let k=0;k<3;k++){const c=aur[k];
          for(let x=0;x<=W;x+=5){
            const y=H*(.17+k*.07)+Math.sin(x*.004+t*.3*sp+k*2)*H*.05+Math.sin(x*.011-t*.2+k)*H*.025;
            const h=H*(.11+.05*Math.sin(x*.006+t*.45+k));
            cx.fillStyle=rgba(c,.045);cx.fillRect(x,y-h,5,h);
            cx.fillStyle=rgba(c,.05);cx.fillRect(x,y-h*.45,5,h*.45);
            cx.fillStyle=rgba(c,.16);cx.fillRect(x,y-5,5,5);}}
        cx.globalCompositeOperation="source-over";
        const cols=[mix([26,34,52],acc,.12),mix([14,18,30],acc,.08),[5,7,12]];
        S.layers.forEach((ys,i)=>{const n=ys.length;
          cx.fillStyle=rgba(cols[i],1);cx.beginPath();cx.moveTo(0,H);
          for(let j=0;j<n;j++)cx.lineTo(j/(n-1)*W,ys[j]);cx.lineTo(W,H);cx.closePath();cx.fill();
          if(i===0){cx.save();cx.clip();const cap=cx.createLinearGradient(0,Math.min(...ys),0,Math.min(...ys)+H*.12);
            cap.addColorStop(0,"rgba(235,245,255,.55)");cap.addColorStop(1,"rgba(235,245,255,0)");
            cx.fillStyle=cap;cx.fillRect(0,0,W,H);cx.restore();}
          if(i<2){cx.strokeStyle=rgba(mix([255,255,255],rgb,.25),i?.12:.3);cx.lineWidth=1.2;cx.beginPath();
            for(let j=0;j<n;j++)cx[j?"lineTo":"moveTo"](j/(n-1)*W,ys[j]);cx.stroke();}});
        cx.fillStyle="rgba(255,255,255,.75)";
        for(const f of S.snow){f.y+=f.v*sp*.8;f.x+=Math.sin(t+f.d)*.35;
          if(f.y>H){f.y=-4;f.x=rnd(0,W);}cx.beginPath();cx.arc(f.x,f.y,f.r,0,TAU);cx.fill();}}},

    /* Rotating magic circle, rising embers, lightning */
    arcane:{
      init(){S.glyphs=Array.from({length:28},()=>Array.from({length:3},()=>[rnd(-1,1),rnd(-1,1),rnd(-1,1),rnd(-1,1)]));
        S.embers=Array.from({length:80},()=>this.ember(true));S.bolt=null;S.next=0;},
      ember(any){return{x:rnd(0,W),y:any?rnd(0,H):H+10,v:rnd(.3,1.3),r:rnd(.8,2.6),w:rnd(0,TAU)};},
      bolt(){const pts=[[rnd(W*.1,W*.9),0]];let [x,y]=pts[0];const ty=H*rnd(.35,.7);
        while(y<ty){x+=rnd(-35,35);y+=rnd(18,42);pts.push([x,y]);}return{pts,life:1};},
      draw(t,sp){
        const ccx=W/2,ccy=H*.42,R=Math.min(W,H)*.34;
        const g=cx.createRadialGradient(ccx,ccy,0,ccx,ccy,Math.max(W,H)*.8);
        g.addColorStop(0,rgba(acc,.14));g.addColorStop(1,"rgba(0,0,0,0)");cx.fillStyle=g;cx.fillRect(0,0,W,H);
        cx.save();cx.translate(ccx,ccy);cx.globalCompositeOperation="lighter";
        cx.strokeStyle=rgba(acc,.5);cx.shadowColor=rgba(acc,1);cx.shadowBlur=14;cx.lineWidth=1.4;
        const ring=(r)=>{cx.beginPath();cx.arc(0,0,r,0,TAU);cx.stroke();};
        cx.save();cx.rotate(t*.08*sp);ring(R);ring(R*.86);
        for(let i=0;i<96;i++){const a=i/96*TAU,l=i%4?R*.95:R*.9;
          cx.beginPath();cx.moveTo(Math.cos(a)*R,Math.sin(a)*R);cx.lineTo(Math.cos(a)*l,Math.sin(a)*l);cx.stroke();}
        S.glyphs.forEach((gl,i)=>{const a=i/S.glyphs.length*TAU;cx.save();
          cx.translate(Math.cos(a)*R*.93,Math.sin(a)*R*.93);cx.rotate(a+Math.PI/2);cx.beginPath();
          for(const[x1,y1,x2,y2]of gl){cx.moveTo(x1*6,y1*6);cx.lineTo(x2*6,y2*6);}cx.stroke();cx.restore();});
        cx.restore();
        cx.save();cx.rotate(-t*.12*sp);cx.strokeStyle=rgba(rgb,.45);ring(R*.62);
        cx.beginPath();for(let i=0;i<=7;i++){const a=i*3/7*TAU-Math.PI/2;cx[i?"lineTo":"moveTo"](Math.cos(a)*R*.86,Math.sin(a)*R*.86);}
        cx.stroke();cx.restore();
        cx.save();cx.rotate(t*.2*sp);ring(R*.32);
        cx.beginPath();for(let i=0;i<=6;i++){const a=i/6*TAU;cx[i?"lineTo":"moveTo"](Math.cos(a)*R*.32,Math.sin(a)*R*.32);}
        cx.stroke();cx.restore();
        cx.restore();cx.shadowBlur=0;
        cx.globalCompositeOperation="lighter";
        for(let i=0;i<S.embers.length;i++){const e=S.embers[i];e.y-=e.v*sp;e.x+=Math.sin(t*1.5+e.w)*.4;
          if(e.y<-10)S.embers[i]=this.ember(false);
          cx.fillStyle=rgba(rgb,.35+.35*Math.sin(t*4+e.w));cx.beginPath();cx.arc(e.x,e.y,e.r,0,TAU);cx.fill();}
        if(t>S.next){S.bolt=this.bolt();S.next=t+rnd(3,8)/sp;}
        if(S.bolt&&S.bolt.life>0){const b=S.bolt;
          cx.fillStyle=rgba(acc,.07*b.life);cx.fillRect(0,0,W,H);
          cx.strokeStyle=rgba(mix([255,255,255],acc,.35),b.life);cx.lineWidth=2;cx.shadowColor=rgba(acc,1);cx.shadowBlur=18;
          cx.beginPath();b.pts.forEach(([x,y],i)=>cx[i?"lineTo":"moveTo"](x,y));cx.stroke();cx.shadowBlur=0;b.life-=.05;}
        cx.globalCompositeOperation="source-over";}},

    /* Stars streaking past like hyperspace */
    warp:{
      trails:true,
      init(){S.s=Array.from({length:400},()=>this.star(true));cx.fillStyle="#000";cx.fillRect(0,0,W,H);},
      star(any){return{x:rnd(-1,1),y:rnd(-1,1),z:any?rnd(.05,1):1};},
      draw(t,sp){cx.fillStyle="rgba(0,0,0,.32)";cx.fillRect(0,0,W,H);
        const mx=W/2,my=H/2,f=Math.max(W,H)*.32;cx.lineCap="round";
        for(let i=0;i<S.s.length;i++){const s=S.s[i],pz=s.z;s.z-=.004*sp;
          if(s.z<=.02){S.s[i]=this.star(false);continue;}
          const x=mx+s.x/s.z*f,y=my+s.y/s.z*f;
          if(x<0||x>W||y<0||y>H){S.s[i]=this.star(false);continue;}
          const k=1-s.z;cx.strokeStyle=rgba(mix([255,255,255],rgb,.6*(1-k)),k);cx.lineWidth=k*2.4;
          cx.beginPath();cx.moveTo(mx+s.x/pz*f,my+s.y/pz*f);cx.lineTo(x,y);cx.stroke();}}},

    /* Slow-drifting colored space clouds */
    nebula:{
      init(){S.stars=stars(220);S.b=Array.from({length:6},(_,i)=>({x:rnd(0,W),y:rnd(0,H),
        r:rnd(.35,.6)*Math.max(W,H),vx:rnd(-.15,.15),vy:rnd(-.12,.12),c:i%3}));},
      draw(t,sp){drawStars(S.stars,t);cx.globalCompositeOperation="lighter";
        for(const b of S.b){b.x+=b.vx*sp;b.y+=b.vy*sp;
          if(b.x<-b.r*.3||b.x>W+b.r*.3)b.vx*=-1;if(b.y<-b.r*.3||b.y>H+b.r*.3)b.vy*=-1;
          const c=[rgb,acc,mix(rgb,acc,.5)][b.c],g=cx.createRadialGradient(b.x,b.y,0,b.x,b.y,b.r);
          g.addColorStop(0,rgba(c,.17));g.addColorStop(1,rgba(c,0));cx.fillStyle=g;cx.fillRect(0,0,W,H);}
        cx.globalCompositeOperation="source-over";}},
  };

  function size(){const dpr=Math.min(devicePixelRatio||1,2);W=innerWidth;H=innerHeight;
    cv.width=W*dpr;cv.height=H*dpr;cx.setTransform(dpr,0,0,dpr,0,0);S={};
    if(SCENES[scene].init)SCENES[scene].init();kick();}
  function color(){const cs=getComputedStyle(document.documentElement);
    rgb=hex(cs.getPropertyValue("--particle"),rgb);acc=hex(cs.getPropertyValue("--accent"),acc);
    fnd=hex(cs.getPropertyValue("--found"),fnd);kick();}
  function burst(){for(let i=0;i<90;i++){const a=rnd(0,TAU),v=rnd(2,6);
    sparks.push({x:W/2,y:H*.22,vx:Math.cos(a)*v,vy:Math.sin(a)*v,life:1});}}

  function frame(now){
    raf=0;const sc=SCENES[scene];
    if(document.hidden){cx.clearRect(0,0,W,H);return;}
    const t=now/1000,sp=mode==="watching"?1.7:mode==="found"?2.8:1;
    if(!sc.trails)cx.clearRect(0,0,W,H);
    if(sc.draw)sc.draw(t,sp);
    if(sparks.length){cx.globalCompositeOperation="lighter";
      for(const s of sparks){s.x+=s.vx;s.y+=s.vy;s.vx*=.97;s.vy*=.97;s.life-=.012;
        cx.fillStyle=rgba(fnd,Math.max(0,s.life));cx.beginPath();cx.arc(s.x,s.y,2.2,0,TAU);cx.fill();}
      cx.globalCompositeOperation="source-over";sparks=sparks.filter(s=>s.life>0);}
    if(!sc.still||sparks.length)raf=requestAnimationFrame(frame);
  }
  function kick(){if(!raf)raf=requestAnimationFrame(frame);}
  addEventListener("resize",size);
  addEventListener("pointermove",e=>{mouse.x=e.clientX;mouse.y=e.clientY;});
  addEventListener("pointerleave",()=>{mouse.x=mouse.y=-9999;});
  document.addEventListener("visibilitychange",kick);
  return{color,
    scene(name){scene=SCENES[name]?name:"particles";cx.clearRect(0,0,W,H);size();},
    mode(m){if(m==="found"&&mode!=="found")burst();mode=m;kick();},
    start(){color();size();}};
})();

/* ===== Your own wallpaper ===== */
const WP=(()=>{
  const box=document.getElementById("wp"),img=document.getElementById("wpImg"),vid=document.getElementById("wpVid");
  let info={has:false,mime:""};
  async function load(){try{info=await api("/wallpaper_info");}catch(e){info={has:false};}}
  function show(on){
    if(!on||!info.has){box.style.display="none";vid.pause();vid.removeAttribute("src");vid.load();img.removeAttribute("src");return;}
    const url="/wallpaper?t="+TOKEN+"&v="+(info.v||0);box.style.display="block";
    if(info.mime.startsWith("video/")){img.style.display="none";vid.style.display="block";
      if(!vid.src.endsWith(url)){vid.src=url;}vid.play().catch(()=>{});}
    else{vid.style.display="none";vid.pause();img.style.display="block";img.src=url;}
  }
  return{load,show,get has(){return info.has;}};
})();

function setBg(name){
  store("qw-bg",name);
  document.querySelectorAll(".bgb").forEach(b=>b.classList.toggle("sel",b.dataset.bg===name));
  document.getElementById("wpRow").style.display=name==="custom"?"block":"none";
  WP.show(name==="custom");FX.scene(name);
}
document.querySelectorAll(".bgb").forEach(b=>b.onclick=()=>setBg(b.dataset.bg));

async function pickWallpaper(){   // opens the normal file window
  const msg=document.getElementById("wpMsg");msg.textContent="Choose a file in the window that opened...";
  const r=await api("/pick_wallpaper",{});msg.textContent=r.msg;
  await WP.load();setBg("custom");
}
async function clearWallpaper(){await api("/wallpaper_clear",{});await WP.load();
  document.getElementById("wpMsg").textContent="Wallpaper removed.";setBg("custom");}

(async function(){
  const reduce=matchMedia("(prefers-reduced-motion: reduce)").matches;
  FX.start();setTheme(document.documentElement.dataset.theme);
  await WP.load();
  setBg(stored("qw-bg")||(reduce?"off":"particles"));
})();


/* ===== Releases & drops ===== */
const MON=["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
let newsData={items:[]},newsCat="all",newsLimit=15;
function ago(iso){if(!iso)return"";const m=Math.round((Date.now()-new Date(iso))/60000);
  if(m<60)return m<=1?"just now":m+" min ago";const h=Math.round(m/60);if(h<24)return h+" hr ago";
  const d=Math.round(h/24);return d===1?"yesterday":d+" days ago";}
const KIND_WORD={"Release":"Drops","Pre-orders open":"Pre-orders open","Prerelease events":"Prerelease events start","Restock":"Restocks"};
function newsRow(i){
  const a=document.createElement("a");a.className="news";a.rel="noopener noreferrer";
  a.onclick=e=>{e.preventDefault();if(/^https?:\/\//.test(a.href))api("/open_url",{url:a.href});};
  a.href=/^https?:\/\//.test(i.link)?i.link:"#";
  const p=i.date.split("-");
  const box=document.createElement("div");box.className="nd"+(i.past?"":" up")+(i.exact?"":" vague");
  box.innerHTML="<small></small><b></b>";
  box.querySelector("small").textContent=MON[+p[1]-1];box.querySelector("b").textContent=i.exact?+p[2]:p[0];
  const body=document.createElement("div");
  const t=document.createElement("div");t.className="nt";t.textContent=i.product;
  const wn=document.createElement("div");wn.className="nwhen";
  const verb=i.past?(i.kind==="Release"?"Dropped":i.kind):(KIND_WORD[i.kind]||i.kind);
  wn.textContent=(i.exact?verb+" "+i.label:(i.past?verb+" ":"Expected ")+i.label)+(i.also&&i.also.length?" (one source says "+i.also.join(" or ")+")":"");
  const tags=document.createElement("div");tags.className="ntags";
  const tag=(txt,cls)=>{if(!txt)return;const e=document.createElement("span");e.className="ntag "+(cls||"");e.textContent=txt;tags.append(e);};
  tag(i.status,"st-"+i.status.toLowerCase());
  tag(i.where,i.where==="Pokémon Center"?"cat-pkc":"");
  if(i.kind!=="Release")tag(i.kind,"k-"+i.kind.toLowerCase().replace(/[^a-z]/g,""));
  const m=document.createElement("div");m.className="nm";
  m.textContent=(i.sources.length>1?"Sources: ":"Source: ")+i.sources.join(", ");
  a.title=i.evidence||"";
  body.append(t,wn,tags,m);a.append(box,body);return a;
}
function newsMatch(i){
  if(newsCat==="all")return true;
  if(newsCat==="confirmed")return i.status==="Confirmed";
  if(newsCat==="predicted")return i.status==="Predicted";
  if(newsCat==="pkc")return i.where==="Pokémon Center";
  if(newsCat==="store")return i.where!=="Stores & online"&&i.where!=="Pokémon Center";
  return true;
}
function renderNews(){
  const list=document.getElementById("newsList");list.innerHTML="";
  const items=(newsData.items||[]).filter(i=>i.product&&newsMatch(i));
  const up=items.filter(i=>!i.past),past=items.filter(i=>i.past).reverse();
  const group=(label,arr)=>{if(!arr.length)return;const g=document.createElement("div");g.className="ngroup";
    g.textContent=label;list.append(g);arr.forEach(i=>list.append(newsRow(i)));};
  group("Coming up",up.slice(0,newsLimit));
  group("Just released",past.slice(0,5));
  if(!items.length){const e=document.createElement("div");e.className="sub";e.style.marginTop="10px";
    e.textContent=newsData.updated?"No dates found for this right now.":"Finding release dates...";list.append(e);}
  document.getElementById("newsMore").style.display=up.length>newsLimit?"inline-block":"none";
  const u=newsData.updated;
  document.getElementById("newsUpdated").textContent=u?`Last checked ${new Date(u).toLocaleString([], {weekday:"short",hour:"numeric",minute:"2-digit"})} · checks every 30 min · Confirmed = firm date from a trusted source, Predicted = expected or rumored`:"Not checked yet";
  const errs=newsData.errors||[];
  document.getElementById("newsErr").textContent=errs.length?"Couldn't check: "+errs.join(" · ")+". Using what it found there last time.":"";
}
document.querySelectorAll("#newsChips button").forEach(b=>b.onclick=()=>{newsCat=b.dataset.c;newsLimit=15;
  document.querySelectorAll("#newsChips button").forEach(x=>x.classList.toggle("sel",x===b));renderNews();});
async function loadNews(){try{newsData=await api("/news");renderNews();}catch(e){}}
async function refreshNews(){
  const b=document.getElementById("newsBtn");b.disabled=true;b.textContent="Checking...";
  try{newsData=await api("/news_refresh",{});renderNews();}catch(e){}
  b.disabled=false;b.textContent="Refresh";
}
loadNews();setInterval(loadNews,60000);

/* ===== Computer health ===== */
const HC_HELP={protect:"Protect: if the computer runs hot or out of memory, Guardian Monitor slows down, then pauses the store checkers, and if it gets very hot it pauses everything until it has cooled down for 10 minutes. You get a ping each time.",
  warn:"Warn only: you get a ping if the computer runs hot or out of memory, but nothing is slowed down or paused.",
  off:"Off: it still shows the readings here, but never pings, slows down or pauses."};
function renderHealth(h,mode){
  const T=h.temp_c!=null?Math.round(h.temp_c)+"°C":(h.thermal?h.thermal[0].toUpperCase()+h.thermal.slice(1):"n/a");
  document.getElementById("hcTemp").textContent=T;
  document.getElementById("hcCpu").textContent=h.cpu!=null?h.cpu+"%":"n/a";
  document.getElementById("hcMem").textContent=h.mem_free!=null?h.mem_free+"%":"n/a";
  document.getElementById("hcClean").textContent=h.cleaned||0;
  document.getElementById("hcStatus").textContent=h.line+(h.level!=="ok"&&h.note?": "+h.note:"");
  document.getElementById("hcChecked").textContent=h.checked?"checked "+ago(new Date(h.checked*1000).toISOString()):"first check in a moment";
  document.querySelectorAll("#hcMode button").forEach(b=>b.classList.toggle("sel",b.dataset.m===mode));
  document.getElementById("hcModeHelp").textContent=HC_HELP[mode]||"";
}
document.querySelectorAll("#hcMode button").forEach(b=>b.onclick=async()=>{
  await api("/save",{health_mode:b.dataset.m});refresh();});

/* ===== Phone controls ===== */
function showPhoneLinks(s){
  const box=document.getElementById("phoneLinks");box.innerHTML="";
  if(!s.ntfy_topic){box.textContent="Add your ntfy topic below first.";return;}
  ["start","stop","status","log"].forEach(w=>{const d=document.createElement("div");
    const b=document.createElement("b");b.textContent=w[0].toUpperCase()+w.slice(1)+": ";
    const c=document.createElement("code");c.textContent="https://ntfy.sh/"+s.ntfy_topic+"-cmd/publish?message="+w;
    c.style.userSelect="all";c.style.wordBreak="break-all";d.append(b,c);box.append(d);});
}
async function sendControls(){const m=document.getElementById("phoneMsg");m.textContent="Sending...";
  try{const r=await api("/phone_controls",{});m.textContent=r.msg;}catch(e){m.textContent="Couldn't send.";}}

/* ===== Updates ===== */
let upd={};
function showUpdate(){
  const b=document.getElementById("updBanner");
  b.style.display=upd.available?"block":"none";
  document.getElementById("updTitle").textContent="Update "+(upd.latest||"")+" is ready";
  document.getElementById("updNotes").textContent=upd.notes||"";
  document.getElementById("updBtn").textContent=upd.available?"Update to "+upd.latest:"Check for updates";
  const m=document.getElementById("updMsg");
  if(!m.dataset.busy)m.textContent="Version "+upd.current+(upd.error?" · "+upd.error:upd.checked&&!upd.available?" · up to date":"");
}
async function loadUpdate(){try{upd=await api("/update_status");showUpdate();}catch(e){}}
async function doUpdate(){
  const m=document.getElementById("updMsg");m.dataset.busy="1";
  document.querySelectorAll("#updBtn,#updBanner button").forEach(b=>b.disabled=true);
  m.textContent=upd.available?"Downloading the update...":"Checking...";
  try{
    if(!upd.available){upd=await api("/update_check",{});delete m.dataset.busy;showUpdate();
      if(upd.available)m.textContent="Update "+upd.latest+" is ready. Click Update again to install it.";
      return;}
    const r=await api("/update_apply",{});m.textContent=r.msg;
    if(r.ok){const want="ok "+r.latest;
      const t=setInterval(async()=>{try{const x=await (await fetch("/ping")).text();
        if(x.trim()===want){clearInterval(t);location.reload();}}catch(e){}},2000);
      return;}
  }catch(e){m.textContent="Couldn't update right now. Try again in a minute.";}
  finally{document.querySelectorAll("#updBtn,#updBanner button").forEach(b=>b.disabled=false);}
  delete m.dataset.busy;
}
loadUpdate();setInterval(loadUpdate,5*60000);

/* ===== Target & Walmart ===== */
const CHIP={in:"In stock at MSRP",out:"Out of stock",third:"Third-party seller",over:"Above MSRP limit",
  nolimit:"No retail price on file",notretail:"Import / repack",
  open:"Drawing open",upcoming:"Coming up",closed:"Closed",unknown:"Status unclear",invite:"Drawing/invite open",invite_soon:"Drawing coming up"};
function money(v){return v==null?"":"$"+Number(v).toFixed(2);}
function checkerRow(i,isDraw){
  const a=document.createElement("a");a.className="crow";a.href=/^https?:\/\//.test(i.url||"")?i.url:"#";
  a.onclick=e=>{e.preventDefault();if(a.href!=="#"&&/^https?:/.test(a.href))api("/open_url",{url:a.href});};
  const n=document.createElement("div");n.className="cname";n.textContent=i.name;
  const m=document.createElement("div");m.className="ntags";
  const tag=(t,c)=>{if(!t)return;const e=document.createElement("span");e.className="ntag "+(c||"");e.textContent=t;m.append(e);};
  tag(CHIP[i.status]||i.status,"st-"+i.status);
  if(i.price!=null)tag(money(i.price));
  if(isDraw&&i.status!=="closed"&&i.msrp_ok===false)tag("Above MSRP limit");
  if(i.when)tag(i.when,i.status==="closed"?"":"k-when");
  a.append(n,m);return a;
}
function renderCheckers(cs){
  if(!cs)return;
  renderStoreChips(cs);
  for(const k of ["target","walmart","bestbuy"]){
    const c=cs[k];if(!c)continue;
    let t=c.message||"";
    if(c.last&&c.status!=="off")t+=" · checked "+ago(new Date(c.last*1000).toISOString());
    if(c.next&&c.status==="on")t+=" · next "+new Date(c.next*1000).toLocaleTimeString([],{hour:"numeric",minute:"2-digit"});
    document.getElementById(k+"Status").textContent=t;
    const list=document.getElementById(k+"List");list.innerHTML="";
    if(c.status!=="off")(c.items||[]).forEach(i=>list.append(checkerRow(i,k==="walmart")));
    document.getElementById(k+"Now").disabled=c.status==="off"||c.status==="checking";
  }
}
["target_enabled","walmart_enabled","bestbuy_enabled"].forEach(id=>document.getElementById(id).onchange=()=>save());
async function shopTest(k){
  await save();
  const el=document.getElementById(k+"TestMsg");el.textContent="Sending...";
  const r=await api("/"+k+"_test",{});el.textContent=r.msg;setTimeout(()=>el.textContent="",6000);
}
async function bestbuyTest(){
  await save();
  const el=document.getElementById("bestbuyTestMsg");el.textContent="Sending...";
  const r=await api("/bestbuy_test",{});el.textContent=r.msg;setTimeout(()=>el.textContent="",6000);
}
async function targetTest(){
  await save();
  const el=document.getElementById("targetTestMsg");el.textContent="Sending...";
  const r=await api("/target_test",{});el.textContent=r.msg;setTimeout(()=>el.textContent="",6000);
}
const TABS=["pkc","stores","alerts","drops","system"];
function showTab(t){
  if(!TABS.includes(t))t="pkc";
  document.querySelectorAll("section.tab").forEach(x=>x.classList.toggle("on",x.dataset.tab===t));
  document.querySelectorAll("#tabs button").forEach(b=>b.classList.toggle("on",b.dataset.go===t));
  try{localStorage.setItem("gmTab",t);}catch(e){}
}
document.querySelectorAll("#tabs button").forEach(b=>b.onclick=()=>{showTab(b.dataset.go);window.scrollTo({top:0,behavior:"smooth"});});
if(/[?&]all=1/.test(location.search))document.body.classList.add("alltabs");
{let t="pkc";try{t=localStorage.getItem("gmTab")||"pkc";}catch(e){}showTab(t);}
// the Store checkers card mirrors each store's own switch
document.querySelectorAll("[data-mirror]").forEach(m=>{
  const src=document.getElementById(m.dataset.mirror);
  m.onchange=()=>{src.checked=m.checked;save();};
});
function syncMirrors(){document.querySelectorAll("[data-mirror]").forEach(m=>{m.checked=document.getElementById(m.dataset.mirror).checked;});}
const STORE_NAMES={target:"Target",bestbuy:"Best Buy",walmart:"Walmart"};
const STATE_WORD={on:"on",checking:"checking",error:"problem",paused:"paused",waiting:"waiting",off:"off"};
function renderStoreChips(cs){
  const box=document.getElementById("storeChips");if(!cs){return;}
  box.innerHTML="";
  for(const k of ["target","bestbuy","walmart"]){
    const c=cs[k];if(!c)continue;
    const b=document.createElement("button");b.className="schip "+(c.status||"off");
    const i=document.createElement("i");b.append(i,document.createTextNode(STORE_NAMES[k]+" · "+(STATE_WORD[c.status]||c.status)));
    b.title=c.message||"";b.onclick=()=>{showTab("stores");document.getElementById(k+"Card").scrollIntoView({behavior:"smooth",block:"start"});};
    box.append(b);
  }
  syncMirrors();
}
async function checkNow(k){document.getElementById(k+"Now").disabled=true;await api("/checker_now",{which:k});setTimeout(refresh,600);}
refresh();setInterval(refresh,2000);
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send(self, code, body, ctype="application/json"):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def host_ok(self):
        return self.headers.get("Host", "") in (f"127.0.0.1:{PORT}", f"localhost:{PORT}")

    def authed(self):
        return self.headers.get("X-Token") == TOKEN

    def status_json(self):
        with lock:
            d = {k: (list(v) if isinstance(v, list) else v) for k, v in state.items()}
        d["settings"] = load_settings()
        d["alerting"] = bool(watcher_thread and watcher_thread.is_alive()) and not ack_event.is_set()
        d["schedule"] = schedule_info()
        d["checkers"] = checkers_info()
        d["health"] = {"line": health_line(), "level": health["level"], "note": health["note"],
                       "thermal": health["thermal"], "temp_c": health["temp_c"], "cpu": health["cpu"],
                       "mem_free": health["mem_free"], "cleaned": health["cleaned"], "checked": health.get("checked"),
                       "busy_minutes": health["busy_streak"]}
        return json.dumps(d)

    def serve_wallpaper(self):
        meta = wallpaper_meta()
        if not meta:
            return self.send(404, '{"error":"no wallpaper"}')
        size = os.path.getsize(WALLPAPER_FILE)
        start, end, code = 0, size - 1, 200
        rng = self.headers.get("Range", "")
        if rng.startswith("bytes="):        # Safari needs this to play videos
            first, _, last = rng[6:].split(",")[0].strip().partition("-")
            try:
                if first:
                    start = int(first)
                    end = int(last) if last else size - 1
                else:
                    start = max(0, size - int(last))
                end = min(end, size - 1)
                code = 206
            except ValueError:
                pass
            if start > end:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.end_headers()
                return
        self.send_response(code)
        self.send_header("Content-Type", meta["mime"])
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Cache-Control", "no-store")
        if code == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        try:
            with open(WALLPAPER_FILE, "rb") as f:
                f.seek(start)
                left = end - start + 1
                while left > 0:
                    chunk = f.read(min(1024 * 1024, left))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def save_wallpaper(self):
        mime = self.headers.get("X-Type", "")
        length = int(self.headers.get("Content-Length") or 0)
        if not (mime.startswith("image/") or mime.startswith("video/")) or "/" not in mime:
            return self.send(200, json.dumps({"ok": False, "msg": "Pick a picture or video file."}))
        if length <= 0 or length > MAX_WALLPAPER:
            return self.send(200, json.dumps({"ok": False, "msg": "That file is too big (300 MB max)."}))
        os.makedirs(DATA_DIR, exist_ok=True)
        tmp = WALLPAPER_FILE + ".part"
        with open(tmp, "wb") as f:
            left = length
            while left > 0:
                chunk = self.rfile.read(min(1024 * 1024, left))
                if not chunk:
                    break
                f.write(chunk)
                left -= len(chunk)
        if left:
            os.remove(tmp)
            return self.send(200, json.dumps({"ok": False, "msg": "Upload didn't finish. Try again."}))
        os.replace(tmp, WALLPAPER_FILE)
        with open(WALLPAPER_META, "w") as f:
            json.dump({"mime": mime, "v": int(time.time())}, f)
        log("New wallpaper saved.")
        return self.send(200, json.dumps({"ok": True, "msg": "Wallpaper set!"}))

    def do_GET(self):
        if not self.host_ok():
            return self.send(403, '{"error":"forbidden"}')
        path, _, query = self.path.partition("?")
        if path == "/wallpaper":
            if parse_qs(query).get("t", [""])[0] != TOKEN:
                return self.send(403, '{"error":"forbidden"}')
            return self.serve_wallpaper()
        if path == "/news":
            if not self.authed():
                return self.send(403, '{"error":"forbidden"}')
            return self.send(200, json.dumps(load_news()))
        if path == "/wallpaper_info":
            if not self.authed():
                return self.send(403, '{"error":"forbidden"}')
            meta = wallpaper_meta()
            return self.send(200, json.dumps({"has": bool(meta), **(meta or {})}))
        if path == "/ping":
            return self.send(200, f"ok {VERSION}", "text/plain")
        if path == "/drill":
            drill["hits"] += 1
            body = ("<h1>Guardian Monitor drill</h1><p>You are now in the waiting room. "
                    "Please keep your browser open. (This is a test page, not a real queue.)</p>"
                    if drill["hits"] >= 2 else
                    "<h1>Test Store</h1><p>Booster boxes, collections and more. "
                    "This fake store turns into a queue in a few seconds.</p>")
            return self.send(200, "<!doctype html><title>Guardian Monitor drill</title>"
                             "<body style='font:18px -apple-system,sans-serif;padding:40px'>" + body,
                             "text/html; charset=utf-8")
        if path in ("/", "/index.html"):          # also "/?app=1", which the window app uses
            return self.send(200, PAGE.replace("__TOKEN__", TOKEN), "text/html; charset=utf-8")
        if path == "/update_status":
            if not self.authed():
                return self.send(403, '{"error":"forbidden"}')
            return self.send(200, json.dumps(update_state))
        if path == "/status":
            if not self.authed():
                return self.send(403, '{"error":"forbidden"}')
            return self.send(200, self.status_json())
        self.send(404, '{"error":"not found"}')

    def do_POST(self):
        if self.path == "/quit_local" and self.host_ok() and \
                secrets.compare_digest(self.headers.get("X-QW-Key", ""), WINDOW_KEY):
            self.send(200, '{"ok":true}')
            log("Window closed. Shutting down.")
            threading.Thread(target=lambda: (time.sleep(0.3), shutdown(quit_window=False)), daemon=True).start()
            return
        if not self.host_ok() or not self.authed():
            return self.send(403, '{"error":"forbidden"}')
        if self.path == "/wallpaper":
            return self.save_wallpaper()
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except Exception:
            body = {}

        if self.path == "/start":
            manual_start()
        elif self.path == "/stop":
            stop_watching()
        elif self.path == "/ack":
            ack_event.set()
        elif self.path == "/login_save":
            cur = load_settings()
            user = str(body.get("user", "")).strip()
            ok, msg = kc_save(cur["watch_url"], user, str(body.get("password", "")))
            if ok:
                if cur["login_user"] and cur["login_user"] != user:
                    kc_delete(cur["watch_url"], cur["login_user"])
                save_settings(clean_settings({"login_user": user, "login_saved": True}))
                log(f"Login saved to Windows Credential Manager for {site_host(cur['watch_url'])}.")
            return self.send(200, json.dumps({"ok": ok, "msg": msg}))
        elif self.path == "/login_forget":
            cur = load_settings()
            if cur["login_user"]:
                kc_delete(cur["watch_url"], cur["login_user"])
            save_settings(clean_settings({"login_user": "", "login_saved": False, "auto_login": False}))
            log("Saved login removed.")
            return self.send(200, json.dumps({"ok": True, "msg": "Login removed from Windows Credential Manager."}))
        elif self.path == "/news_refresh":
            return self.send(200, json.dumps(refresh_news()))
        elif self.path == "/wallpaper_clear":
            for f in (WALLPAPER_FILE, WALLPAPER_META):
                try:
                    os.remove(f)
                except FileNotFoundError:
                    pass
            return self.send(200, '{"ok":true}')
        elif self.path == "/test_discord":
            s = load_settings()
            if not s["discord_webhook"]:
                return self.send(200, json.dumps({"ok": False, "msg": "Add your Discord webhook URL first."}))
            preview = ("\u2705 **Test from Guardian Monitor.** This is what the queue alert will say "
                       "(the @ is left off for the test):\n\n" + discord_text(s, "discord_queue_msg"))
            threading.Thread(target=notify_discord, args=(s, preview, False, True), daemon=True).start()
            return self.send(200, json.dumps({"ok": True, "msg": "Sent a preview to Discord."}))
        elif self.path == "/discord_profile":
            ok, msg = set_discord_profile(load_settings(), str(body.get("name", "")).strip(),
                                          str(body.get("avatar", "")))
            return self.send(200, json.dumps({"ok": ok, "msg": msg}))
        elif self.path == "/test":
            s = load_settings()
            log("Sending test pings...")
            threading.Thread(target=send_all, daemon=True,
                             args=(s, "Guardian Monitor test", "Test ping. Your alerts work!",
                                   "\u2705 Test: Pok\u00e9mon Center queue alerts are set up for this channel.",
                                   False)).start()
        elif self.path == "/target_test":
            return self.send(200, json.dumps({"ok": True, "msg": send_target_test(load_settings())}))
        elif self.path == "/bestbuy_test":
            return self.send(200, json.dumps({"ok": True, "msg": send_bestbuy_test(load_settings())}))
        elif self.path == "/walmart_test":
            return self.send(200, json.dumps({"ok": True, "msg": send_walmart_test(load_settings())}))
        elif self.path == "/checker_now":
            which = body.get("which")
            if which in checkers:
                checkers[which]["wake"].set()
            return self.send(200, '{"ok":true}')
        elif self.path == "/save":
            s = clean_settings(body)
            save_settings(s)
            log("Settings saved.")
            for n, c in checkers.items():          # react to on/off right away
                if (c["status"] == "off") == s[f"{n}_enabled"]:
                    c["next"] = None
                    c["wake"].set()
            return self.send(200, json.dumps({"settings": s}))
        elif self.path == "/quit":
            self.send(200, '{"ok":true}')
            threading.Thread(target=lambda: (time.sleep(0.5), shutdown()), daemon=True).start()
            return
        elif self.path == "/drill_start":
            ok = start_drill()
            return self.send(200, json.dumps({"ok": ok, "msg": "Drill started." if ok else "Stop watching first, then run the drill."}))
        elif self.path == "/pick_wallpaper":
            return self.send(200, json.dumps(pick_wallpaper()))
        elif self.path == "/pick_picture":
            return self.send(200, json.dumps(pick_picture()))
        elif self.path == "/phone_controls":
            s = load_settings()
            if not s["ntfy_topic"]:
                return self.send(200, json.dumps({"ok": False, "msg": "Add your ntfy topic first."}))
            ok = phone_reply(s, status_title(), status_text())
            return self.send(200, json.dumps({"ok": ok, "msg": "Sent! Check your phone." if ok
                                              else "Couldn't reach ntfy. Try again."}))
        elif self.path == "/update_check":
            check_update()
            return self.send(200, json.dumps(update_state))
        elif self.path == "/update_apply":
            ok, msg = apply_update()
            return self.send(200, json.dumps({"ok": ok, "msg": msg, **update_state}))
        elif self.path == "/open_url":
            return self.send(200, json.dumps({"ok": open_link(body.get("url", ""))}))
        else:
            return self.send(404, '{"error":"not found"}')
        self.send(200, self.status_json())


# ---------------- Guardian Monitor rename (runs once) ----------------
BRAND = "Guardian Monitor"


def apply_branding():
    """Give your Discord alerts the Guardian Monitor name and picture, once."""
    s = load_settings()
    if s["brand_v1"]:
        return
    if not s["discord_webhook"]:
        s = load_settings()            # fresh copy, so nothing you just saved gets overwritten
        s["brand_v1"] = True
        save_settings(s)
        return
    import base64
    try:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "guardian_avatar.png"), "rb") as f:
            pic = "data:image/png;base64," + base64.b64encode(f.read()).decode()
    except Exception:
        pic = None
    ok, msg = set_discord_profile(s, BRAND, pic)
    if ok:
        s = load_settings()
        if not s["discord_name"] or s["discord_name"].lower() in ("queue watcher", "pkc queue watcher"):
            s["discord_name"] = BRAND
        s["brand_v1"] = True
        save_settings(s)
        log("Discord bot is now called Guardian Monitor, with the new picture.")
    else:
        log(f"Couldn't update the Discord bot's name/picture yet: {msg}")


ROLE_IDS = {"all": "1558918973842661506", "pkc": "1561325379963068538", "target": "1561324538686611467", "walmart": "1561324931176734760",
            "bestbuy": "1561847155239420086"}


def apply_roles():
    """Once: ping the server's alert roles (Pokémon Center / Target Restocks / Walmart Raffles / Best Buy)."""
    with settings_lock:
        s = load_settings()
        if not s["brand_v2"]:                   # every Discord post says "Guardian Monitor" (once; you can change it after)
            if s["discord_name"] != BRAND:
                log(f'Discord bot name changed from "{s["discord_name"] or "(webhook default)"}" to "{BRAND}".')
            s["discord_name"], s["brand_v2"] = BRAND, True
            save_settings(s)
        if not s["discord_role_all"] and not s.get("roles_v4"):
            s["discord_role_all"], s["roles_v4"] = ROLE_IDS["all"], True
            save_settings(s)
        if not s["discord_role_bestbuy"] and not s.get("roles_v2"):
            s["discord_role_bestbuy"], s["roles_v2"] = ROLE_IDS["bestbuy"], True
            save_settings(s)
        if s["roles_v1"]:
            return
        s["discord_mention"], s["discord_user_id"] = "role", ROLE_IDS["pkc"]
        s["discord_role_target"] = s["discord_role_target"] or ROLE_IDS["target"]
        s["discord_role_walmart"] = s["discord_role_walmart"] or ROLE_IDS["walmart"]
        if s["target_mention"] in ("here", "everyone"):
            s["target_mention"] = "same"
        s["roles_v1"] = True
        save_settings(s)
    log("Discord alerts now ping the Pokémon Center, Target Restocks and Walmart Raffles roles.")


# ---------------- phone controls (ntfy) ----------------
# Send "start", "stop" or "status" to your private command topic (your topic + "-cmd") and Guardian Monitor
# does it and answers on your normal topic. Replies come with Start / Stop / Status buttons.
NTFY_BASE = "https://ntfy.sh"
CMD_EVERY = 10
phone = {"since": None, "last": None}


def cmd_topic(s):
    return f"{s['ntfy_topic']}-cmd"


def cmd_link(s, word):
    return f"{NTFY_BASE}/{cmd_topic(s)}/publish?message={word}"


def control_buttons(s):
    return [{"action": "http", "label": label, "url": f"{NTFY_BASE}/{cmd_topic(s)}", "method": "POST",
             "body": word, "clear": True} for label, word in (("Start", "start"), ("Stop", "stop"),
                                                               ("Status", "status"))]


def short_time(t):
    """'03:31:29 PM' or a timestamp -> '3:31 PM'"""
    try:
        if isinstance(t, (int, float)):
            t = datetime.fromtimestamp(t).strftime("%I:%M %p")
        t = str(t)
        m = _re.match(r"0?(\d{1,2}):(\d\d)(?::\d\d)?\s*([AP]M)", t)
        return f"{m.group(1)}:{m.group(2)} {m.group(3)}" if m else t
    except Exception:
        return ""


CHECKER_ICON = {"on": "✅", "checking": "🔄", "error": "⚠️", "paused": "⏸️", "off": "➖", "waiting": "🌙"}


def status_title():
    st = state["status"]
    msg = state["message"]
    if st == "found":
        return "🚨 Queue found!"
    if st == "watching":
        return "🟠 Resting after an error" if "Resting" in msg else "🟢 Watching Pokémon Center"
    if st == "error":
        return "🔴 Stopped"
    return "⏸️ Not watching"


def status_text():
    with lock:
        st = dict(state)
    lines = []
    if st["status"] == "watching":
        if "Resting" in st["message"]:
            lines.append(st["message"].replace(" Still watching.", ""))
        bits = []
        if st["checks"]:
            bits.append(f"{st['checks']} checks")
        if st["last_check"]:
            bits.append(f"last {short_time(st['last_check'])}")
        lines.append("Pokémon Center: " + (" · ".join(bits) if bits else "starting up"))
    elif st["status"] in ("error", "found"):
        lines.append(st["message"].replace(" Press Start to try again.", ""))
    try:
        si = schedule_info()
        if si["in_window"] and si["window_end"]:
            lines.append(f"Drop hours until {si['window_end']}")
        elif si["next"] and st["status"] != "watching":
            lines.append(f"Next auto-start: {si['next']}")
    except Exception:
        pass
    lines.append(health_line())
    for name, c in checkers.items():
        label = SITE_NAMES.get(name, name.title())
        if c["status"] == "off":
            if name == "bestbuy":
                lines.append(f"{CHECKER_ICON['off']} {label}: off on this computer")
            continue
        msg = (c.get("message") or c["status"]).replace("Problem: ", "")
        msg = msg.split(". It'll")[0]
        if c["status"] == "on" and c.get("last"):
            msg += f" · {short_time(c['last'])}"
        elif c["status"] == "checking":
            msg = msg.replace("Checking...", "starting a check")
            if c.get("last_result"):
                msg += f" (last: {c['last_result']} · {short_time(c['last_done'])})"
        lines.append(f"{CHECKER_ICON.get(c['status'], '•')} {label}: {msg}")
    return "\n".join(lines)


def info_ping(s, title, msg):
    """A calm heads-up: phone (with Start/Stop/Status buttons) + this computer. Never Discord."""
    phone_reply(s, title, msg)
    notify_mac(msg)


def phone_reply(s, title, msg):
    """A normal (not urgent) ntfy message with Start / Stop / Status buttons.
    Sent as JSON so emoji and accents in the title come through."""
    if not s["ntfy_topic"]:
        return False
    try:
        body = {"topic": s["ntfy_topic"], "title": title, "message": msg, "priority": 3,
                "actions": control_buttons(s)}
        req = urllib.request.Request(f"{NTFY_BASE}/", data=json.dumps(body).encode("utf-8"),
                                     headers={"Content-Type": "application/json"})
        fetch(req, timeout=10)
        return True
    except Exception as e:
        log(f"Phone message failed: {e}")
        return False


CHANNEL_KEYS = {"pkc": "", "queue": "", "pokemon": "", "main": "", "target": "target", "walmart": "walmart",
                "bestbuy": "bestbuy", "bb": "bestbuy", "all": "all", "every": "all"}
CHANNEL_LABEL = {"": "Pokémon Center queue", "target": "Target restock", "walmart": "Walmart drawing",
                 "bestbuy": "Best Buy restock", "all": "All"}
WEBHOOK_RX = _re.compile(r"^https://(?:ptb\.|canary\.)?discord(?:app)?\.com/api/webhooks/\d+/[\w-]+$")


def phone_setup(s, text):
    """'webhook bestbuy <link>' or 'role target <id>' from your phone. Returns True if it was one."""
    parts = text.strip().split()
    if len(parts) < 3 or parts[0].lower() not in ("webhook", "hook", "role"):
        return False
    kind = parts[0].lower()
    which = parts[1].lower().replace("-", "").replace("_", "")
    rest = parts[2:]
    if which == "best" and rest and rest[0].lower() == "buy":          # "best buy" as two words
        which, rest = "bestbuy", rest[1:]
    if which not in CHANNEL_KEYS or not rest:
        phone_reply(s, "🤔 Didn't catch that", "Send it like:\nwebhook bestbuy https://discord.com/api/webhooks/...\n"
                    "role bestbuy 123456789012345678\n(names: pkc, target, walmart, bestbuy)")
        return True
    ch = CHANNEL_KEYS[which]
    label = CHANNEL_LABEL[ch]
    if ch == "all" and kind != "role":
        phone_reply(s, "🤔 Didn't catch that", "\"all\" only works with role, like: role all 1558918973842661506")
        return True
    value = rest[0].strip("<>")
    with settings_lock:
        fresh = load_settings()
        if kind in ("webhook", "hook"):
            if not WEBHOOK_RX.match(value):
                phone_reply(s, "⚠️ That's not a Discord webhook link",
                            "It should start with https://discord.com/api/webhooks/ — copy it again from the channel's "
                            "Integrations → Webhooks.")
                return True
            fresh["discord_webhook" + (f"_{ch}" if ch else "")] = value
            if ch in ("target", "walmart", "bestbuy"):
                fresh[f"{ch}_enabled"] = True          # giving a store its channel turns that checker on here
        else:
            rid = "".join(c for c in value if c.isdigit())
            if not 15 <= len(rid) <= 25:
                phone_reply(s, "⚠️ That's not a role ID", "Role IDs are long numbers, like 1561325145199616150.")
                return True
            if ch:
                fresh[f"discord_role_{ch}"] = rid
            else:
                fresh["discord_mention"], fresh["discord_user_id"] = "role", rid
        save_settings(fresh)
    s = load_settings()
    if kind in ("webhook", "hook"):
        log(f"{label} alerts now post to a new Discord channel (set from your phone).")
        notify_discord(s, f"\u2705 Guardian Monitor will post **{label}** alerts in this channel.",
                       mention=False, force=True, channel=ch or None)
        phone_reply(s, f"✅ {label} channel set", f"{label} alerts now go to that Discord channel"
                    + (f", and the {SITE_NAMES[ch]} checker is on for this computer" if ch else "")
                    + ". A ✅ message was just posted there so you can check.")
    else:
        log(f"{label} alerts now @ a role (set from your phone).")
        phone_reply(s, f"✅ {label} role set", f"{label} alerts will @ that role.")
    return True


def phone_switch(s, text):
    """'bestbuy on', 'target off', 'walmart on'... Returns True if it was one."""
    words = text.strip().lower().replace("best buy", "bestbuy").split()
    if len(words) != 2 or words[1] not in ("on", "off") or \
            words[0] not in ("target", "walmart", "bestbuy", "bb"):
        return False
    ch = {"bb": "bestbuy"}.get(words[0], words[0])
    with settings_lock:
        fresh = load_settings()
        fresh[f"{ch}_enabled"] = words[1] == "on"
        save_settings(fresh)
    checkers[ch]["wake"].set()
    log(f"{SITE_NAMES[ch]} checker turned {words[1]} from your phone.")
    phone_reply(s, f"{'✅' if words[1] == 'on' else '⏹️'} {SITE_NAMES[ch]} checker {words[1]}",
                f"The {SITE_NAMES[ch]} checker is now {words[1]} on this computer.")
    return True


TEST_NAMES = {"pkc": "pkc", "queue": "pkc", "pokemon": "pkc", "target": "target", "bestbuy": "bestbuy", "bb": "bestbuy",
              "walmart": "walmart"}


def phone_test(s, text):
    """'test target', 'test best buy', 'test all'... posts a clearly marked TEST (no @) to that channel."""
    words = text.strip().lower().replace("best buy", "bestbuy").split()
    if len(words) != 2 or words[0] != "test" or (words[1] not in TEST_NAMES and words[1] != "all"):
        return False
    which = list(dict.fromkeys(TEST_NAMES.values())) if words[1] == "all" else [TEST_NAMES[words[1]]]
    done = []
    for k in which:
        try:
            SHOP_TESTS[k](load_settings())
            done.append({"pkc": "Pokémon Center"}.get(k, SITE_NAMES.get(k, k)))
        except Exception as e:
            log(f"Test for {k} didn't send: {e}")
    phone_reply(s, "🧪 Test sent", f"Sent a TEST (no @) for: {', '.join(done) or 'nothing'}. Check those Discord channels.")
    return True


def phone_command(s, text):
    if phone_setup(s, text) or phone_switch(s, text) or phone_test(s, text):
        return True
    word = text.strip().lower().strip("!. ")
    log(f'Phone command: "{word[:20]}"')
    if word in ("start", "go", "watch"):
        if state["status"] == "watching":
            return phone_reply(s, "👍 Already watching", status_text())
        manual_start()
        time.sleep(8)                      # give it a moment to open the page and do its first check
        return phone_reply(s, "▶️ Started", status_text())
    if word in ("stop", "pause"):
        stop_watching()
        time.sleep(1)
        return phone_reply(s, "⏹️ Stopped", "Not watching Pokémon Center. Tap Start to watch again.\n"
                           + status_text())
    if word in ("log", "logs"):
        with lock:
            lines = state["log"][-15:]
        return phone_reply(s, "📋 Recent activity", "\n".join(l[:160] for l in lines) or "Nothing yet.")
    if word in ("status", "?", "check", "how"):
        return phone_reply(s, status_title(), status_text())
    if word in ("update", "upgrade", "install"):
        check_update()
        if not update_state["available"]:
            if update_state.get("error"):
                return phone_reply(s, "⚠️ Couldn't check for updates", update_state["error"])
            return phone_reply(s, "👍 Already up to date", f"Guardian Monitor {VERSION} is the newest version.")
        phone_reply(s, f"⬇️ Updating to {update_state['latest']}",
                    "Installing now. It restarts by itself and picks back up where it was.")
        ok, msg = apply_update()
        if not ok:
            return phone_reply(s, "⚠️ Update didn't install", msg)
        return True
    return phone_reply(s, "Guardian Monitor", "Use the buttons, or send start, stop, status, log or update. "
                       "Store checkers: bestbuy on/off, target on/off, walmart on/off. "
                       "Tests: test target, test bestbuy, test walmart, test pkc, test all.")


def phone_control_loop():
    """Check the command topic every few seconds. Commands sent while the app was closed are ignored."""
    phone["since"] = str(int(time.time()))
    while True:
        s = load_settings()
        if s["ntfy_topic"] and s["phone_control"]:
            try:
                url = f"{NTFY_BASE}/{cmd_topic(s)}/json?poll=1&since={phone['since']}"
                with fetch(url, timeout=15) as r:
                    lines = r.read().decode("utf-8", "replace").splitlines()
                for line in lines:
                    if not line.strip():
                        continue
                    m = json.loads(line)
                    if m.get("event") != "message":
                        continue
                    phone["since"] = m.get("id") or phone["since"]
                    if m.get("id") == phone["last"]:
                        continue
                    phone["last"] = m.get("id")
                    threading.Thread(target=phone_command, args=(s, m.get("message", "")), daemon=True).start()
            except Exception:
                pass
        time.sleep(CMD_EVERY)


# ---------------- computer health guard ----------------
# Every minute: how hot and how busy is this computer? If it's running hot or out of memory,
# Guardian Monitor eases off on its own, and if it gets really hot it pauses everything until
# the computer cools down, then picks back up. It also clears out any hidden Chrome it left behind.
HEALTH_EVERY = 60
health = {"level": "ok", "thermal": "normal", "cpu": None, "mem_free": None, "temp_c": None,
          "note": "", "since": time.time(), "busy_streak": 0, "paused_pkc": False, "calm_since": None,
          "cleaned": 0}
LEVELS = ["ok", "warm", "hot", "critical"]
THERMAL_MAC = {0: "normal", 1: "warm", 2: "hot", 3: "critical"}


def read_stats():
    """{'thermal': normal|warm|hot|critical|None, 'cpu': %, 'mem_free': %, 'temp_c': °C or None}"""
    out = {"thermal": None, "cpu": None, "mem_free": None, "temp_c": None}
    try:
        t, _ = subprocess.run(["osascript", "-l", "JavaScript", "-e",
                               'ObjC.import("Foundation"); $.NSProcessInfo.processInfo.thermalState'],
                              capture_output=True, text=True, timeout=15).stdout, None
        if t.strip().isdigit():
            out["thermal"] = THERMAL_MAC.get(int(t.strip()), "normal")
    except Exception:
        pass
    try:
        out["cpu"] = round(min(100.0, os.getloadavg()[0] / (os.cpu_count() or 1) * 100))
    except Exception:
        pass
    try:
        m = _re.search(r"free percentage:\s*(\d+)%", subprocess.run(["memory_pressure", "-Q"], capture_output=True,
                                                                     text=True, timeout=15).stdout)
        if m:
            out["mem_free"] = int(m.group(1))
    except Exception:
        pass
    return out


def clean_stray_chrome():
    """Hidden page readers should finish in seconds. Close any that got stuck (older than 3 minutes)."""
    killed = 0
    try:
        r = subprocess.run(["ps", "-axo", "pid=,etime=,command="], capture_output=True, text=True, timeout=15)
        for line in r.stdout.splitlines():
            if "checker-chrome-" not in line or "--headless" not in line:
                continue
            pid, et = line.split()[:2]
            parts = [int(x) for x in _re.split(r"[-:]", et)]
            secs = parts[-1] + 60 * (parts[-2] if len(parts) > 1 else 0) + 3600 * (parts[-3] if len(parts) > 2 else 0) \
                + 86400 * (parts[-4] if len(parts) > 3 else 0)
            if secs > 180:
                os.kill(int(pid), 9)
                killed += 1
    except Exception:
        pass
    return killed


def judge_health(st):
    """Turn the readings into ok / warm / hot / critical, with a plain-words reason."""
    level, why = "ok", ""
    th = st.get("thermal")
    if th in ("warm", "hot", "critical"):
        level, why = th, f"the computer reports it's {'very hot' if th == 'critical' else 'running hot' if th == 'hot' else 'warm'}"
    t = st.get("temp_c")
    if t is not None:
        tl = "critical" if t >= 95 else "hot" if t >= 88 else "warm" if t >= 80 else "ok"
        if LEVELS.index(tl) > LEVELS.index(level):
            level, why = tl, f"it's at {round(t)}°C"
    cpu = st.get("cpu")
    if cpu is not None and cpu >= 90:
        health["busy_streak"] += 1
    else:
        health["busy_streak"] = 0
    if health["busy_streak"] >= 5 and LEVELS.index(level) < LEVELS.index("warm"):   # busy for 5+ minutes
        level, why = "warm", f"the processor has been {cpu}% busy for a while"
    if health["busy_streak"] >= 15 and LEVELS.index(level) < LEVELS.index("hot"):
        level, why = "hot", f"the processor has been maxed out ({cpu}%) for 15+ minutes"
    mf = st.get("mem_free")
    if mf is not None and mf < 8:
        ml = "hot" if mf < 4 else "warm"
        if LEVELS.index(ml) > LEVELS.index(level):
            level, why = ml, f"memory is almost full ({mf}% free)"
    return level, why


def health_mode():
    try:
        return load_settings()["health_mode"]
    except Exception:
        return "protect"


def health_factor():
    """How much to slow down: 1 = normal, 2 = warm (half speed). Only when Safety is set to protect."""
    return 2.0 if health["level"] == "warm" and health_mode() == "protect" else 1.0


def health_paused():
    return health["level"] in ("hot", "critical") and health_mode() == "protect"


def health_line():
    lv = health["level"]
    icon = {"ok": "🟢", "warm": "🟡", "hot": "🟠", "critical": "🔴"}[lv]
    bits = []
    if health["temp_c"] is not None:
        bits.append(f"{round(health['temp_c'])}°C")
    elif health["thermal"]:
        bits.append(f"temp {health['thermal']}")
    if health["cpu"] is not None:
        bits.append(f"CPU {health['cpu']}%")
    if health["mem_free"] is not None:
        bits.append(f"{health['mem_free']}% memory free")
    word = {"ok": "Computer OK", "warm": "Computer warm: slowed down", "hot": "Computer hot: checkers paused",
            "critical": "Computer very hot: everything paused"}[lv]
    mode = health_mode()
    if mode == "warn" and lv != "ok":
        word = {"warm": "Computer working hard", "hot": "Computer hot", "critical": "Computer very hot"}[lv] + " (warn only)"
    elif mode == "off":
        word = "Computer safety off"
        icon = "⚪"
    return f"{icon} {word}" + (f" ({' · '.join(bits)})" if bits else "")


def health_tick():
    st = read_stats()
    health.update(thermal=st.get("thermal"), cpu=st.get("cpu"), mem_free=st.get("mem_free"), temp_c=st.get("temp_c"))
    killed = clean_stray_chrome()
    if killed:
        health["cleaned"] += killed
        log(f"Closed {killed} stuck hidden Chrome reader{'s' if killed > 1 else ''}.")
    new, why = judge_health(st)
    old = health["level"]
    s = load_settings()
    health["checked"] = time.time()
    if s["health_mode"] == "off":                 # readings only, no actions, no pings
        health.update(level="ok", note="", calm_since=None)
        if health["paused_pkc"]:
            health["paused_pkc"] = False
            start_watching()
        return
    if s["health_mode"] == "warn":                # tell me, but don't slow down or pause anything
        if LEVELS.index(new) > LEVELS.index(old):
            health.update(level=new, note=why, since=time.time(), calm_since=None)
            icon = {"warm": "🟡", "hot": "🟠", "critical": "🔴"}[new]
            log(f"Computer check: {why}. (Safety is set to warn only, so nothing was slowed down.)")
            info_ping(s, f"{icon} Your computer is {'very hot' if new == 'critical' else 'running hot' if new == 'hot' else 'working hard'}",
                      f"Guardian Monitor noticed {why}. Safety is set to warn only, so it's still running at full speed.")
        elif LEVELS.index(new) < LEVELS.index(old):
            health.update(level=new, note=why, calm_since=None)
        return
    if LEVELS.index(new) > LEVELS.index(old):                     # getting worse: act right away
        health.update(level=new, note=why, since=time.time(), calm_since=None)
        if new == "warm":
            log(f"Computer check: {why}. Slowing everything down to half speed.")
            info_ping(s, "🟡 Easing off your computer", f"Guardian Monitor noticed {why}, so it's checking half as "
                      "often until things settle. Still watching.")
        elif new == "hot":
            log(f"Computer check: {why}. Pausing Target and Walmart; Pokémon Center keeps watching slowly.")
            info_ping(s, "🟠 Your computer is running hot", f"Guardian Monitor noticed {why}. Target and Walmart are "
                      "paused and Pokémon Center is checking at half speed until it cools down.")
        else:
            was = state["status"] == "watching"
            health["paused_pkc"] = was or health["paused_pkc"]
            if was:
                stop_watching()
            log(f"Computer check: {why}. Pausing everything until it cools down.")
            info_ping(s, "🔴 Paused to protect your computer", f"Guardian Monitor noticed {why}, so it paused "
                      "everything. It starts again by itself once the computer has cooled down for 10 minutes. "
                      "If this keeps happening, check that the fans aren't blocked or dusty.")
    elif LEVELS.index(new) < LEVELS.index(old):                   # better: wait 10 calm minutes first
        if health["calm_since"] is None:
            health["calm_since"] = time.time()
        if time.time() - health["calm_since"] >= 600:
            health.update(level=new, note=why, since=time.time(), calm_since=None)
            if new == "ok":
                log("Computer check: back to normal. Full speed again.")
                resumed = ""
                if health["paused_pkc"]:
                    health["paused_pkc"] = False
                    if current_window(s, eastern_now()) or s["schedule_mode"] == "manual":
                        start_watching()
                        resumed = " Pokémon Center watching is back on."
                info_ping(s, "✅ Computer back to normal", "It's cooled down, so Guardian Monitor is back to full "
                          "speed." + resumed)
            else:
                log(f"Computer check: cooling down ({new}).")
    else:
        health["calm_since"] = None


def health_loop():
    time.sleep(15)
    while True:
        try:
            health_tick()
        except Exception as e:
            log(f"Computer check error: {e}")
        time.sleep(HEALTH_EVERY)


# ---------------- updates (the Update button) ----------------
# Fixes are posted to your own GitHub repo. The app checks it, downloads the new files, makes sure
# each one matches its fingerprint (SHA-256) in the update list, swaps them in, and restarts itself.
# Your settings, saved login and wallpaper are never touched.
import hashlib

UPDATE_REPO = "kylerredfield24-sys/queue-watcher"
UPDATE_BASE = f"https://raw.githubusercontent.com/{UPDATE_REPO}/main/"
UPDATE_EVERY = 3 * 60 * 60
SAFE_PATH = _re.compile(r"^[A-Za-z0-9_.\- ]+(?:/[A-Za-z0-9_.\- ]+)*$")
update_state = {"current": VERSION, "latest": None, "available": False, "notes": "", "checked": None,
                "error": "", "busy": False}


def vtuple(v):
    return tuple(int(x) for x in _re.findall(r"\d+", str(v))[:4])


def app_root():
    """Where the app's files live: the .app's Contents folder on a Mac, the install folder on Windows."""
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.dirname(here) if PLATFORM == "mac" else here


def _get(path, limit):
    req = urllib.request.Request(UPDATE_BASE + path, headers={"User-Agent": "QueueWatcher-updater",
                                                              "Cache-Control": "no-cache"})
    with fetch(req, timeout=30) as r:
        return r.read(limit)


def check_update():
    try:
        man = json.loads(_get("version.json", 200000))
        latest = str(man.get("version", ""))
        files = (man.get("files") or {}).get(PLATFORM) or {}
        update_state.update(latest=latest, notes=str(man.get("notes", ""))[:600], error="",
                            available=bool(files) and vtuple(latest) > vtuple(VERSION),
                            checked=datetime.now().astimezone().isoformat())
        return man
    except Exception as e:
        update_state.update(error="Couldn't check for updates right now.",
                            checked=datetime.now().astimezone().isoformat())
        log(f"Update check failed: {e}")
        return None


def apply_update():
    if update_state["busy"]:
        return False, "Already updating..."
    update_state["busy"] = True
    try:
        man = check_update()
        if not man:
            return False, "Couldn't reach the update server. Try again in a minute."
        if not update_state["available"]:
            return False, f"You're up to date ({VERSION})."
        files = man["files"][PLATFORM]
        root = app_root()
        staged = []
        for rel, sha in files.items():
            if not SAFE_PATH.match(rel) or ".." in rel.split("/") or not _re.fullmatch(r"[0-9a-f]{64}", str(sha)):
                return False, "That update looked wrong, so nothing was changed."
            data = _get(f"{PLATFORM}/{rel}", 30 * 1024 * 1024)
            if hashlib.sha256(data).hexdigest() != sha:
                for tmp, _ in staged:
                    os.remove(tmp)
                return False, "A download didn't come through right, so nothing was changed. Try again."
            dest = os.path.join(root, *rel.split("/"))
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            tmp = dest + ".qwnew"
            with open(tmp, "wb") as f:
                f.write(data)
            try:
                os.chmod(tmp, os.stat(dest).st_mode if os.path.exists(dest) else 0o755)
            except Exception:
                pass
            staged.append((tmp, dest))
        for tmp, dest in staged:            # everything downloaded fine: swap it all in
            os.replace(tmp, dest)
        log(f"Updated to {man['version']}. Restarting...")
        threading.Thread(target=restart_app, daemon=True).start()
        return True, f"Updated to {man['version']}. Restarting..."
    except Exception as e:
        log(f"Update failed: {e}")
        return False, "The update didn't finish, so nothing was changed. Try again."
    finally:
        update_state["busy"] = False


def restart_app():
    """Open the app again: its launcher sees the newer version and replaces this running copy."""
    time.sleep(1)
    app = os.path.dirname(app_root())
    if app.endswith(".app"):
        subprocess.Popen(["open", "-n", app])
    else:
        os.execv(sys.executable, [sys.executable, os.path.abspath(__file__)])


def update_ping(s):
    """Once per new version: tell your phone, with an Update now button."""
    latest = update_state["latest"]
    if not s["ntfy_topic"] or not latest or s.get("update_pinged") == latest:
        return
    try:
        body = {"topic": s["ntfy_topic"], "title": f"⬇️ Update {latest} is ready",
                "message": (update_state.get("notes") or "A new version of Guardian Monitor is out.")[:300]
                           + "\nTap Update now to install it (or send \"update\").", "priority": 3,
                "actions": [{"action": "http", "label": "Update now", "url": f"{NTFY_BASE}/{cmd_topic(s)}",
                             "method": "POST", "body": "update", "clear": True},
                            {"action": "http", "label": "Status", "url": f"{NTFY_BASE}/{cmd_topic(s)}",
                             "method": "POST", "body": "status", "clear": True}]}
        req = urllib.request.Request(f"{NTFY_BASE}/", data=json.dumps(body).encode("utf-8"),
                                     headers={"Content-Type": "application/json"})
        fetch(req, timeout=10)
        with settings_lock:
            fresh = load_settings()
            fresh["update_pinged"] = latest
            save_settings(fresh)
    except Exception as e:
        log(f"Couldn't tell your phone about the update: {e}")


def update_loop():
    time.sleep(20)
    while True:
        if check_update() and update_state["available"]:
            log(f"Update {update_state['latest']} is ready. Click Update in the app.")
            update_ping(load_settings())
        time.sleep(UPDATE_EVERY)


# ---------------- Mac window app helpers ----------------
PANEL = os.environ.get("QW_PANEL", "")          # path of the window app, when started from it
PANEL_ID = "com.kyler.queuewatcher.panel"
MEDIA_TYPES = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "gif": "image/gif",
               "webp": "image/webp", "heic": "image/heic", "mp4": "video/mp4", "m4v": "video/mp4",
               "mov": "video/quicktime", "webm": "video/webm"}


def choose_file(prompt, kinds):
    """Show the normal Mac 'choose a file' window. Returns a path, or None if you cancel."""
    types = ", ".join(f'"{k}"' for k in kinds)
    out, err = osa(f'POSIX path of (choose file with prompt "{as_str(prompt)}" of type {{{types}}})',
                   timeout=600)
    return out if out.startswith("/") else None


def sips(*args):
    r = subprocess.run(["sips", *args], capture_output=True, text=True, timeout=60)
    return r.returncode == 0, r.stdout


def pick_wallpaper():
    path = choose_file("Choose a picture or video for the background", ["public.image", "public.movie"])
    if not path:
        return {"ok": False, "msg": "No file chosen."}
    ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    mime = MEDIA_TYPES.get(ext)
    if not mime:
        return {"ok": False, "msg": "Use a JPG, PNG, GIF, WEBP, MP4, MOV, or WEBM file."}
    src = path
    if ext == "heic":                       # browsers can't show HEIC, so convert it
        src = os.path.join(DATA_DIR, "wallpaper-convert.jpg")
        ok, _ = sips("-s", "format", "jpeg", path, "--out", src)
        if not ok:
            return {"ok": False, "msg": "Couldn't convert that HEIC photo."}
        mime = "image/jpeg"
    if os.path.getsize(src) > MAX_WALLPAPER:
        return {"ok": False, "msg": "That file is over 300 MB. Pick a smaller one."}
    os.makedirs(DATA_DIR, exist_ok=True)
    import shutil
    shutil.copyfile(src, WALLPAPER_FILE + ".part")
    os.replace(WALLPAPER_FILE + ".part", WALLPAPER_FILE)
    with open(WALLPAPER_META, "w") as f:
        json.dump({"mime": mime, "v": int(time.time())}, f)
    log("New wallpaper saved.")
    return {"ok": True, "msg": "Wallpaper set!"}


def pick_picture():
    """Choose a bot picture, crop it to a 256px square, and return it as a data URL."""
    import base64
    path = choose_file("Choose a picture for your Discord bot", ["public.image"])
    if not path:
        return {"ok": False, "msg": "No picture chosen."}
    tmp = os.path.join(DATA_DIR, "bot-picture.png")
    os.makedirs(DATA_DIR, exist_ok=True)
    ok, _ = sips("-s", "format", "png", "--resampleHeightWidthMax", "768", path, "--out", tmp)
    if not ok:
        return {"ok": False, "msg": "Couldn't open that picture."}
    ok, info = sips("-g", "pixelWidth", "-g", "pixelHeight", tmp)
    nums = [int(x) for x in _re.findall(r"pixel(?:Width|Height):\s*(\d+)", info)]
    side = min(nums) if len(nums) == 2 else None
    if side:
        sips("--cropToHeightWidth", str(side), str(side), tmp)
    sips("-z", "256", "256", tmp)
    with open(tmp, "rb") as f:
        data = "data:image/png;base64," + base64.b64encode(f.read()).decode()
    return {"ok": True, "msg": "Picture ready. Click Update to send it to Discord.", "data": data}


def open_link(url):
    if str(url).startswith(("http://", "https://")):
        subprocess.Popen(["open", url])
        return True
    return False


def shutdown(quit_window=True):
    """Stop everything and exit. Also closes the window app."""
    stop_event.set()
    ack_event.set()
    checker_stop.set()
    for n in checkers:
        try:
            close_checker_tab(n)
        except Exception:
            pass
    try:
        keep_awake(False)
    except Exception:
        pass
    if quit_window and PANEL:
        osa(f'tell application id "{PANEL_ID}" to quit', timeout=5)
    os._exit(0)


WINDOW_KEY_FILE = os.path.join(DATA_DIR, "window.key")
WINDOW_KEY = secrets.token_hex(16)


def write_window_key():
    """The window app reads this to say 'I was closed'. Only your Mac user can read it."""
    os.makedirs(DATA_DIR, exist_ok=True)
    fd = os.open(WINDOW_KEY_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(WINDOW_KEY)


def panel_running():
    try:
        r = subprocess.run(["pgrep", "-f", PANEL.rstrip("/") + "/Contents/MacOS/"],
                           capture_output=True, text=True, timeout=10)
        return r.returncode == 0
    except Exception:
        return None


def panel_monitor(url):
    """Quitting the window app (⌘Q) quits everything. If the window app never shows up,
    open the control panel in the browser instead, so you're never left without one."""
    seen, started, misses = False, time.time(), 0
    while True:
        alive = panel_running()
        if alive:
            seen, misses = True, 0
        elif alive is False:
            if not seen and time.time() - started > 45:
                log("The window app didn't open, so the control panel opened in your browser.")
                open_link(url)
                return
            if seen:
                misses += 1
                if misses >= 2:
                    log("Window closed. Shutting down.")
                    shutdown(quit_window=False)
        time.sleep(3)


def main():
    load_settings()
    try:
        server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    except OSError:
        osa(f'display alert "Guardian Monitor can\'t start" message "Another program is using port {PORT}. '
            f'Restart your Mac and try again."')
        return
    url = f"http://127.0.0.1:{PORT}/"
    log("Guardian Monitor is running.")
    threading.Thread(target=scheduler_loop, daemon=True).start()
    threading.Thread(target=news_loop, daemon=True).start()
    threading.Thread(target=update_loop, daemon=True).start()
    threading.Thread(target=apply_branding, daemon=True).start()
    apply_roles()
    threading.Thread(target=health_loop, daemon=True).start()
    threading.Thread(target=phone_control_loop, daemon=True).start()
    for n in checkers:
        threading.Thread(target=checker_loop, args=(n,), daemon=True).start()
    write_window_key()
    if PANEL:
        threading.Thread(target=panel_monitor, args=(url,), daemon=True).start()
    else:
        try:
            subprocess.Popen(["open", url])
        except Exception:
            print("Open this in your browser:", url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


# ---------------- Windows ----------------
# Swap the Mac-only parts (AppleScript, Keychain, ...) for Windows ones. See winplat.py.
import winplat as _winplat
_winplat.install(globals())


if __name__ == "__main__":
    main()
