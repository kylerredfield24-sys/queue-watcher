"""
Guardian Monitor news bot: posts new Pokémon TCG news to your Discord and pings your Pokémon role.

Reads public news feeds (PokeBeach, PokéGuardian, PokéJungle), keeps only card-game news (new sets,
products, promos, prereleases, restocks), and posts each new story once. Runs on GitHub on a schedule
(see .github/workflows/news.yml), so your computer can be off.

Settings (environment variables):
  DISCORD_WEBHOOK_NEWS  the news channel's webhook link (a GitHub secret)
  NEWS_ROLE             role ID to ping (default: the server's Pokémon role; "none" = no ping)
  NEWS_TEST             "true" = post a clearly marked test using the newest real story
  STATE_FILE            where it remembers which stories it already posted
"""
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

FEEDS = [("PokeBeach", "https://www.pokebeach.com/feed"),
         ("PokéGuardian", "https://www.pokeguardian.com/feed"),
         ("PokéJungle", "https://pokejungle.net/feed/")]
if os.environ.get("NEWS_FEEDS"):          # "Name|url,Name|url" (used by tests)
    FEEDS = [tuple(x.split("|", 1)) for x in os.environ["NEWS_FEEDS"].split(",") if "|" in x]
WEBHOOK = os.environ.get("DISCORD_WEBHOOK_NEWS", "").strip()
STATE_FILE = os.environ.get("STATE_FILE", "news_state.json")
POKEMON_ROLE = "1558918973842661506"
ROLE = "".join(c for c in (os.environ.get("NEWS_ROLE") or POKEMON_ROLE) if c.isdigit())
BOT_NAME = "Guardian Monitor"
BOT_PIC = "https://raw.githubusercontent.com/kylerredfield24-sys/queue-watcher/main/assets/guardian.png"
MAX_POSTS = 6                              # most stories per check (a backlog never floods the channel)
MAX_AGE_DAYS = 3                           # never post old stories
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/141.0.0.0 Safari/537.36")
# Card-game news only (PokéJungle also covers the video games, anime, etc.)
TCG = re.compile(r"\btcg\b|trading card|card game|\bcards?\b|\bset\b|expansion|booster|elite trainer|\betb\b|"
                 r"\bpromo|prerelease|pre-release|collection box|\bcollection\b|premium collection|\btins?\b|"
                 r"restock|pok[eé]mon center|\bex\b|illustration rare|\bsar\b|card list|reveal", re.I)
NOT_TCG = re.compile(r"\bpok[eé]mon go\b|\bunite\b|\banime\b|\bepisode\b|\bmovie\b|\bscarlet and violet dlc\b|"
                     r"\bpok[eé]mon sleep\b|\bmasters ex\b|\bcafe\b|\bplush\b|\bvideo game\b|\bswitch\b|\bstream\b",
                     re.I)
# Leaks and rumors get an "Unconfirmed" label and no ping; only confirmed news pings your role.
RUMOR = re.compile(r"\brumou?r|\bleak|speculat|reportedly|unconfirmed|\bdatamin|\bpossibl|\bmight\b|could be|"
                   r"\bhint|\bteas", re.I)
NS = {"media": "http://search.yahoo.com/mrss/", "content": "http://purl.org/rss/1.0/modules/content/",
      "atom": "http://www.w3.org/2005/Atom"}


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/rss+xml, application/xml, */*"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read()
        except (urllib.error.URLError, TimeoutError):
            if attempt == 2:
                raise
            time.sleep(4 * (attempt + 1))


def clean(text, n=300):
    text = re.sub(r"<[^>]+>", " ", html.unescape(text or ""))
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= n else text[:n].rsplit(" ", 1)[0] + "…"


def parse_date(s):
    from email.utils import parsedate_to_datetime
    from datetime import datetime, timezone
    if not s:
        return None
    try:
        return parsedate_to_datetime(s).timestamp()
    except Exception:
        pass
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc).timestamp()
    except Exception:
        return None


def items_from(source, raw):
    """[{id, title, link, summary, image, when, source, cats}] from an RSS or Atom feed."""
    root = ET.fromstring(raw)
    out = []
    entries = root.findall(".//item") or root.findall(".//atom:entry", NS)
    for e in entries:
        t = lambda tag: (e.findtext(tag, default="", namespaces=NS) or "").strip()
        link = t("link")
        if not link:
            le = e.find("atom:link", NS)
            link = le.get("href", "") if le is not None else ""
        body = t("content:encoded") or t("description") or t("atom:summary") or t("atom:content")
        img = ""
        for tag in ("media:content", "media:thumbnail", "enclosure"):
            m = e.find(tag, NS)
            if m is not None and m.get("url") and not m.get("type", "image").startswith(("audio", "video")):
                img = m.get("url")
                break
        if not img:
            m = re.search(r'<img[^>]+src="([^"]+)"', body or "")
            img = html.unescape(m.group(1)) if m else ""
        cats = " ".join((c.text or "") for c in e.findall("category"))
        out.append({"id": t("guid") or t("atom:id") or link, "title": clean(t("title") or t("atom:title"), 250),
                    "link": link, "summary": clean(body), "image": img,
                    "when": parse_date(t("pubDate") or t("atom:published") or t("atom:updated")),
                    "source": source, "cats": cats})
    return out


def is_tcg(it):
    blob = f"{it['title']} {it['cats']}"
    if it["source"] == "PokeBeach":              # PokeBeach is almost all TCG; just skip obvious non-card posts
        return not NOT_TCG.search(it["title"]) or bool(re.search(r"\btcg\b|card", blob, re.I))
    return bool(TCG.search(blob)) and not (NOT_TCG.search(it["title"]) and not re.search(r"\btcg\b|card", blob, re.I))


def unconfirmed(it):
    return bool(RUMOR.search(it["title"]) or re.search(r"\brumou?rs?\b|\bleaks?\b", it["cats"], re.I))


def embed(it, test=False):
    rumor = unconfirmed(it)
    lead = "\U0001F9EA TEST: " if test else ("\u26A0\uFE0F Unconfirmed: " if rumor else "\U0001F4F0 ")
    e = {"title": lead + it["title"][:230], "url": it["link"],
         "description": it["summary"][:350], "color": 0xF1C40F if test else (0xE67E22 if rumor else 0x3498DB),
         "footer": {"text": f"Guardian Monitor · {it['source']}" + (" · rumor/leak, not official yet" if rumor else "")}}
    if it["image"].startswith("http"):
        e["image"] = {"url": it["image"]}
    return e


def post(payload):
    if not WEBHOOK:
        print("No DISCORD_WEBHOOK_NEWS set; would have posted:", json.dumps(payload)[:300])
        return
    data = json.dumps(payload).encode("utf-8")
    for attempt in range(3):
        req = urllib.request.Request(WEBHOOK, data=data, headers={"Content-Type": "application/json",
                                                                  "User-Agent": "GuardianMonitorNews/1.0"})
        try:
            urllib.request.urlopen(req, timeout=20)
            return
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < 2:
                time.sleep(float(e.headers.get("Retry-After", "2")) + 0.5)
                continue
            raise


def payload(embeds, ping=True):
    out = {"username": BOT_NAME, "avatar_url": BOT_PIC, "embeds": embeds, "allowed_mentions": {"parse": []}}
    if ping and ROLE:
        out["content"] = f"<@&{ROLE}>"
        out["allowed_mentions"] = {"roles": [ROLE]}
    return out


def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return None


def main():
    test = os.environ.get("NEWS_TEST") == "true"
    if test and not WEBHOOK:
        print("TEST FAILED: the DISCORD_WEBHOOK_NEWS secret is missing. Add it in Settings -> Secrets -> Actions.")
        return 1
    stories, problems = [], []
    for name, url in FEEDS:
        try:
            stories += [x for x in items_from(name, get(url)) if is_tcg(x)]
        except Exception as e:
            problems.append(f"{name} ({str(e)[:80]})")
    for p in problems:
        print("Couldn't read", p)
    if not stories:
        print("No stories read this time.")
        return 1 if test else 0
    stories.sort(key=lambda x: x["when"] or 0, reverse=True)
    if test:
        post(payload([embed(stories[0], test=True)], ping=False))
        print("Test post sent using:", stories[0]["title"])
        return 0
    st = load_state()
    if st is None:                                   # first run: remember what's there, post nothing old
        with open(STATE_FILE, "w") as f:
            json.dump({"seen": [x["id"] for x in stories][-800:]}, f)
        post(payload([{"title": "✅ Guardian Monitor news is on",
                       "description": "New Pokémon TCG news (sets, products, promos, prereleases, restocks) "
                                      f"from {', '.join(n for n, _ in FEEDS)} will post here.",
                       "color": 0x2ECC71}], ping=False))
        print(f"First run: remembered {len(stories)} stories.")
        return 0
    seen = set(st.get("seen", []))
    now = time.time()
    new = [x for x in stories if x["id"] not in seen and (x["when"] is None or now - x["when"] < MAX_AGE_DAYS * 86400)]
    # the same story from two sites: keep the first one
    out, titles = [], set()
    for x in sorted(new, key=lambda x: x["when"] or 0):
        key = re.sub(r"[^a-z0-9]+", " ", x["title"].lower())[:60]
        if key in titles:
            continue
        titles.add(key)
        out.append(x)
    out = out[-MAX_POSTS:]
    for x in out:
        post(payload([embed(x)], ping=not unconfirmed(x)))
        time.sleep(1.2)
    seen.update(x["id"] for x in stories)
    with open(STATE_FILE, "w") as f:
        json.dump({"seen": list(seen)[-800:]}, f)
    print(f"Read {len(stories)} TCG stories, posted {len(out)} new.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
