"""
Gator's Card Den restock bot.

Reads Gator's Card Den's public store listing and posts to your Discord:
  - Pokémon sealed (English, Japanese, Chinese): back in stock or new.
  - Pokémon singles (English, Japanese, Chinese): every card that gets listed (or relisted) on the website.
Their website and their register share the same stock, so in-store restocks of listed items show up too.

Runs on GitHub's computers on a schedule (see .github/workflows/gators.yml), so your own computer can be off.
Settings come from environment variables:
  DISCORD_WEBHOOK          your sealed channel's webhook link (stored as a GitHub secret)
  DISCORD_WEBHOOK_SINGLES  optional: a separate channel for singles (blank = same as above)
  GATORS_ROLE / GATORS_SINGLES_ROLE  optional: a role ID to ping (blank = no ping)
  STATE_FILE       where it remembers what was in stock last time (default: gators_state.json)
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request

STORE = os.environ.get("GATORS_STORE", "https://www.gatorscardden.com")
COLLECTIONS = [c for c in os.environ.get(
    "GATORS_COLLECTIONS", "pokemon-english,pokemon-japanese,s-chinese-pokemon-tcg").split(",") if c]
SINGLES = [c for c in os.environ.get(
    "GATORS_SINGLES", "raw-singles,japanese-singles,pokemon-chinese-singles").split(",") if c]
STATE_FILE = os.environ.get("STATE_FILE", "gators_state.json")
WEBHOOK = os.environ.get("DISCORD_WEBHOOK", "").strip()
WEBHOOK_SINGLES = os.environ.get("DISCORD_WEBHOOK_SINGLES", "").strip()
MAX_CARDS = 200                    # most single-card posts in one check (a huge batch gets a summary line)
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/141.0.0.0 Safari/537.36")
GREEN, BLUE = 0x2ECC71, 0x3498DB
BOT_NAME = "Guardian Monitor"
GATORS_ROLE_ID = "1561325145199616150"       # the server's "Gators Card Den" role
ROLE = "".join(c for c in (os.environ.get("GATORS_ROLE") or GATORS_ROLE_ID) if c.isdigit())   # "none" = no ping
SINGLES_ROLE = "".join(c for c in (os.environ.get("GATORS_SINGLES_ROLE") or ROLE) if c.isdigit())
BOT_PIC = "https://raw.githubusercontent.com/kylerredfield24-sys/queue-watcher/main/assets/guardian.png"


def get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503) and attempt < 2:
                time.sleep(5 * (attempt + 1))
                continue
            raise
        except urllib.error.URLError:
            if attempt < 2:
                time.sleep(5)
                continue
            raise


def store_products(collections=None, singles=False):
    """Every product in the given collections: {product_id: {...}}."""
    out = {}
    for col in (COLLECTIONS if collections is None else collections):
        page = 1
        while page <= 20:
            data = get_json(f"{STORE}/collections/{col}/products.json?limit=250&page={page}")
            items = data.get("products", [])
            if not items:
                break
            for p in items:
                kind = (p.get("product_type") or "").lower()
                if singles and kind and "pokemon" not in kind and "pokémon" not in kind:
                    continue                      # singles collections can mix games; keep Pokémon only
                img = (p.get("images") or [{}])[0].get("src", "")
                out[str(p["id"])] = {
                    "title": p.get("title", "").strip(),
                    "url": f"{STORE}/products/{p.get('handle', '')}",
                    "image": img,
                    "updated": max([v.get("updated_at") or "" for v in p.get("variants", [])] + [p.get("published_at") or ""]),
                    "variants": {str(v["id"]): {"title": v.get("title", ""), "price": v.get("price", ""),
                                                "available": bool(v.get("available"))}
                                 for v in p.get("variants", [])},
                    "single": singles,
                }
            if len(items) < 250:
                break
            page += 1
    return out


def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return None


def save_state(products, last_alert=None, resent_once=None, carried=None):
    old = load_state() or {}
    prods = {pid: {"title": p["title"], "single": bool(p.get("single")),
                   "variants": {vid: v["available"] for vid, v in p["variants"].items()}}
             for pid, p in products.items()}
    prods.update(carried or {})
    seen = set(old.get("seen") or old.get("products", {}).keys()) | set(products)
    state = {"saved": int(time.time()), "last_alert": last_alert or old.get("last_alert"),
             "resent_once": resent_once or old.get("resent_once"),
             "singles_on": bool(SINGLES) and (old.get("singles_on") or any(p.get("single") for p in products.values())),
             "seen": sorted(seen), "products": prods}
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)


def money(p):
    try:
        return f"${float(p):,.2f}"
    except Exception:
        return str(p)


def single_embed(kind, p, variants):
    lines = []
    for v in variants[:4]:
        opt = v["title"] if v["title"] and v["title"] != "Default Title" else "Card"
        lines.append(f"**{opt[:120]}:** {money(v['price'])}")
    e = {"title": ("🔁 Single back at Gator's" if kind == "back" else "🃏 New single at Gator's"),
         "description": f"**[{p['title'][:220]}]({p['url']})**\n" + "\n".join(lines),
         "url": p["url"], "color": GREEN if kind == "back" else BLUE,
         "footer": {"text": "Guardian Monitor · Gator's Card Den singles · listed on their website"}}
    if p["image"]:
        e["thumbnail"] = {"url": p["image"]}
    return e


def embed(kind, p, variants):
    if p.get("single"):
        return single_embed(kind, p, variants)
    v = variants[0]
    lines = [f"**Price:** {money(v['price'])}"]
    names = [x["title"] for x in variants if x["title"] and x["title"] != "Default Title"]
    if names:
        lines.insert(0, f"**Option:** {', '.join(names)[:200]}")
    lines.append("**Where:** in store & online")
    e = {"title": ("🎉 Back in stock at Gator's" if kind == "back" else "🆕 New at Gator's"),
         "description": f"**[{p['title'][:200]}]({p['url']})**\n" + "\n".join(lines),
         "url": p["url"], "color": GREEN if kind == "back" else BLUE,
         "footer": {"text": "Guardian Monitor · Gator's Card Den, 520 W Walnut St, Perkasie"}}
    if p["image"]:
        e["thumbnail"] = {"url": p["image"]}
    return e


def changes(old, new):
    """[(kind, product, [variants])] for things that came back in stock or are brand new (and in stock)."""
    found = []
    old_p = old.get("products", {})
    seen = set(old.get("seen") or old_p.keys())
    for pid, p in new.items():
        avail = [v for v in p["variants"].values() if v["available"]]
        if not avail:
            continue
        if pid not in old_p:
            found.append(("back" if pid in seen else "new", p, avail))
            continue
        before = old_p[pid].get("variants", {})
        back = [v for vid, v in p["variants"].items() if v["available"] and before.get(vid) is False]
        new_variant = [v for vid, v in p["variants"].items() if v["available"] and vid not in before]
        if back or new_variant:
            found.append(("back", p, back + new_variant))
    return found


def post(payload, hook=None):
    hook = hook or WEBHOOK
    if not hook:
        print("No DISCORD_WEBHOOK set; would have posted:", json.dumps(payload)[:500])
        return
    data = json.dumps(payload).encode("utf-8")
    for attempt in range(3):
        req = urllib.request.Request(hook, data=data, headers={"Content-Type": "application/json",
                                                                  "User-Agent": "GatorsRestockBot/1.0"})
        try:
            urllib.request.urlopen(req, timeout=20)
            return
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < 2:
                time.sleep(float(e.headers.get("Retry-After", "2")) + 0.5)
                continue
            raise


def test_post(products, hook=None):
    """A clearly marked fake restock, using a real product from the live site, to check everything end to end."""
    pick = next((p for p in products.values() if any(v["available"] for v in p["variants"].values())),
                next(iter(products.values())))
    e = embed("back", pick, list(pick["variants"].values())[:1])
    e["title"] = "🧪 TEST: " + e["title"].split(" ", 1)[1]
    e["description"] = "**This is a test, not a real restock.**\n" + e["description"]
    e["color"] = 0xF1C40F
    post({"username": BOT_NAME, "avatar_url": BOT_PIC, "embeds": [e]}, hook)
    print(f"Test post sent using: {pick['title']} ({len(products)} products read from the live site).")


def alert_payload(embeds, role=None):
    role = ROLE if role is None else role
    out = {"username": BOT_NAME, "avatar_url": BOT_PIC, "embeds": embeds, "allowed_mentions": {"parse": []}}
    if role:                                       # ping a role only once one is set up
        out["content"] = f"<@&{role}>"
        out["allowed_mentions"] = {"roles": [role]}
    return out


def resend_latest(products):
    """Post the most recent sealed restock alert again."""
    st = load_state() or {}
    embeds = st.get("last_alert")
    if not embeds:              # nothing saved yet: use the most recently updated product that's in stock
        live = [p for p in products.values() if not p.get("single")
                and any(v["available"] for v in p["variants"].values())]
        if not live:
            print("Nothing in stock to resend.")
            return 1
        p = max(live, key=lambda x: x["updated"])
        embeds = [embed("back", p, [v for v in p["variants"].values() if v["available"]])]
    embeds = [dict(e, title="🔁 " + e["title"].lstrip("🔁 ")) for e in embeds]
    post(alert_payload(embeds[:10]))
    print(f"Resent: {', '.join(e['description'].split('](')[0].strip('*[') for e in embeds[:10])}")
    return 0


def in_stock(products):
    return sum(1 for p in products.values() if any(v["available"] for v in p["variants"].values()))


def hello(text, hook=None):
    post({"username": BOT_NAME, "avatar_url": BOT_PIC,
          "embeds": [{"title": "✅ Guardian Monitor is watching Gator's", "description": text, "color": GREEN}]}, hook)


def main():
    test = os.environ.get("GATORS_TEST") == "true"
    if test and not WEBHOOK:
        print("TEST FAILED: the DISCORD_WEBHOOK secret is missing. Add it in Settings -> Secrets -> Actions.")
        return 1
    sealed = store_products()
    if not sealed:
        print("The store returned no products; keeping the old list.")
        return 1 if test else 0
    singles = {}
    if SINGLES:
        try:
            singles = store_products(SINGLES, singles=True)
        except Exception as e:             # sealed alerts still go out; singles are kept as they were
            print(f"Couldn't read the singles this time ({e}); trying again next check.")
            singles = None
    s_hook = WEBHOOK_SINGLES or WEBHOOK
    if test:
        test_post(sealed)
        if singles:
            test_post(singles, s_hook)
        return 0                     # a test never changes what it remembers
    if os.environ.get("GATORS_RESEND") == "true":
        return resend_latest(sealed)
    products = {**sealed, **(singles or {})}
    old = load_state()
    if old is None:
        save_state(products)
        hello(f"Watching {len(sealed)} Pokémon sealed products at Gator's Card Den ({in_stock(sealed)} in stock right now). "
              "You'll see a post here when something comes back in stock or a new product is added.")
        if singles:
            hello(f"Watching {len(singles)} Pokémon singles at Gator's Card Den. Every new card they list "
                  "will be posted here.", s_hook)
        print(f"First run: saved {len(products)} products.")
        return 0
    # Something missing for one check is kept for one more (so a hiccup in their listing doesn't look like a restock).
    carried = {}
    for pid, e in old.get("products", {}).items():
        if pid in products:
            continue
        if e.get("single") and singles is None:
            carried[pid] = e
            continue
        if e.get("miss", 0) + 1 < 2:
            carried[pid] = dict(e, miss=e.get("miss", 0) + 1)
    first_singles = bool(singles) and not old.get("singles_on")
    # A one-time "send the latest alert again" request (the file gators-bot/resend_once.txt).
    once = ""
    try:
        once = open(os.environ.get("GATORS_RESEND_FILE") or
                    os.path.join(os.path.dirname(os.path.abspath(__file__)), "resend_once.txt")).read().strip()
    except Exception:
        pass
    if once and old.get("resent_once") != once:
        resend_latest(sealed)
    found = changes(old, products)
    sealed_embeds = [embed(kind, p, vs) for kind, p, vs in found if not p.get("single")]
    single_found = [] if first_singles else [(k, p, vs) for k, p, vs in found if p.get("single")]
    single_embeds = [embed(kind, p, vs) for kind, p, vs in single_found[:MAX_CARDS]]
    if first_singles:
        hello(f"Now watching {len(singles)} Pokémon singles at Gator's Card Den too. Every new card they list "
              "will be posted here.", s_hook)
    if not found and os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch":
        hello(f"Checked {len(sealed)} sealed products ({in_stock(sealed)} in stock)"
              + (f" and {len(singles)} singles" if singles else "")
              + ". Nothing new since the last check. It keeps checking every few minutes.")
    for i in range(0, len(sealed_embeds), 10):          # Discord allows 10 embeds per message
        post(alert_payload(sealed_embeds[i:i + 10]))
        time.sleep(1)
    for i in range(0, len(single_embeds), 10):         # one ping per check, not one per batch of cards
        post(alert_payload(single_embeds[i:i + 10], SINGLES_ROLE if i == 0 else ""), s_hook)
        time.sleep(1)
    if len(single_found) > MAX_CARDS:
        post({"username": BOT_NAME, "avatar_url": BOT_PIC,
              "content": f"…and {len(single_found) - MAX_CARDS} more new singles: <{STORE}/collections/{SINGLES[0]}>"},
             s_hook)
    save_state(products, sealed_embeds[:10] or None, once or None, carried)
    print(f"Checked {len(sealed)} sealed + {len(singles or {})} singles: "
          f"{len(sealed_embeds)} sealed restock/new, {len(single_found)} singles.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
