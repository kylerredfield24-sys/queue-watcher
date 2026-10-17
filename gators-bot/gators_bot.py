"""
Gator's Card Den restock bot.

Reads Gator's Card Den's public store listing (Pokémon sealed: English, Japanese, Chinese) and posts to
your Discord when something comes back in stock or a new product shows up. Their website and their
in-store register share the same stock, so in-store restocks of listed items show up here too.

Runs on GitHub's computers on a schedule (see .github/workflows/gators.yml), so your own computer can be off.
Settings come from environment variables:
  DISCORD_WEBHOOK  your channel's webhook link (stored as a GitHub secret)
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
STATE_FILE = os.environ.get("STATE_FILE", "gators_state.json")
WEBHOOK = os.environ.get("DISCORD_WEBHOOK", "").strip()
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/141.0.0.0 Safari/537.36")
GREEN, BLUE = 0x2ECC71, 0x3498DB


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


def store_products():
    """Every product in the Pokémon collections: {product_id: {...}}."""
    out = {}
    for col in COLLECTIONS:
        page = 1
        while page <= 20:
            data = get_json(f"{STORE}/collections/{col}/products.json?limit=250&page={page}")
            items = data.get("products", [])
            if not items:
                break
            for p in items:
                img = (p.get("images") or [{}])[0].get("src", "")
                out[str(p["id"])] = {
                    "title": p.get("title", "").strip(),
                    "url": f"{STORE}/products/{p.get('handle', '')}",
                    "image": img,
                    "variants": {str(v["id"]): {"title": v.get("title", ""), "price": v.get("price", ""),
                                                "available": bool(v.get("available"))}
                                 for v in p.get("variants", [])},
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


def save_state(products):
    state = {"saved": int(time.time()),
             "products": {pid: {"title": p["title"], "variants": {vid: v["available"] for vid, v in p["variants"].items()}}
                          for pid, p in products.items()}}
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)


def money(p):
    try:
        return f"${float(p):,.2f}"
    except Exception:
        return str(p)


def embed(kind, p, variants):
    v = variants[0]
    lines = [f"**Price:** {money(v['price'])}"]
    names = [x["title"] for x in variants if x["title"] and x["title"] != "Default Title"]
    if names:
        lines.insert(0, f"**Option:** {', '.join(names)[:200]}")
    lines.append("**Where:** in store & online")
    e = {"title": ("🎉 Back in stock at Gator's" if kind == "back" else "🆕 New at Gator's"),
         "description": f"**[{p['title'][:200]}]({p['url']})**\n" + "\n".join(lines),
         "url": p["url"], "color": GREEN if kind == "back" else BLUE,
         "footer": {"text": "Gator's Card Den · 520 W Walnut St, Perkasie"}}
    if p["image"]:
        e["thumbnail"] = {"url": p["image"]}
    return e


def changes(old, new):
    """[(kind, product, [variants])] for things that came back in stock or are brand new (and in stock)."""
    found = []
    old_p = old.get("products", {})
    for pid, p in new.items():
        avail = [v for v in p["variants"].values() if v["available"]]
        if not avail:
            continue
        if pid not in old_p:
            found.append(("new", p, avail))
            continue
        before = old_p[pid].get("variants", {})
        back = [v for vid, v in p["variants"].items() if v["available"] and before.get(vid) is False]
        new_variant = [v for vid, v in p["variants"].items() if v["available"] and vid not in before]
        if back or new_variant:
            found.append(("back", p, back + new_variant))
    return found


def post(payload):
    if not WEBHOOK:
        print("No DISCORD_WEBHOOK set; would have posted:", json.dumps(payload)[:500])
        return
    data = json.dumps(payload).encode("utf-8")
    for attempt in range(3):
        req = urllib.request.Request(WEBHOOK, data=data, headers={"Content-Type": "application/json",
                                                                  "User-Agent": "GatorsRestockBot/1.0"})
        try:
            urllib.request.urlopen(req, timeout=20)
            return
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < 2:
                time.sleep(float(e.headers.get("Retry-After", "2")) + 0.5)
                continue
            raise


def test_post(products):
    """A clearly marked fake restock, using a real product from the live site, to check everything end to end."""
    pick = next((p for p in products.values() if any(v["available"] for v in p["variants"].values())),
                next(iter(products.values())))
    e = embed("back", pick, list(pick["variants"].values())[:1])
    e["title"] = "🧪 TEST: Back in stock at Gator's"
    e["description"] = "**This is a test, not a real restock.**\n" + e["description"]
    e["color"] = 0xF1C40F
    post({"username": "Gator's Restock Watch", "embeds": [e]})
    print(f"Test post sent using: {pick['title']} ({len(products)} products read from the live site).")


def main():
    if os.environ.get("GATORS_TEST") == "true" and not WEBHOOK:
        print("TEST FAILED: the DISCORD_WEBHOOK secret is missing. Add it in Settings -> Secrets -> Actions.")
        return 1
    products = store_products()
    if not products:
        print("The store returned no products; keeping the old list.")
        return 1 if os.environ.get("GATORS_TEST") == "true" else 0
    if os.environ.get("GATORS_TEST") == "true":
        test_post(products)
        return 0                     # a test never changes what it remembers
    old = load_state()
    if old is None:
        save_state(products)
        live = sum(1 for p in products.values() if any(v["available"] for v in p["variants"].values()))
        post({"username": "Gator's Restock Watch", "embeds": [{
            "title": "✅ Gator's restock watch is on",
            "description": f"Watching {len(products)} Pokémon products at Gator's Card Den ({live} in stock right now). "
                           "You'll see a post here when something comes back in stock or a new product is added.",
            "color": GREEN}]})
        print(f"First run: saved {len(products)} products.")
        return 0
    found = changes(old, products)
    embeds = [embed(kind, p, vs) for kind, p, vs in found]
    if not embeds and os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch":
        live = sum(1 for p in products.values() if any(v["available"] for v in p["variants"].values()))
        post({"username": "Gator's Restock Watch", "embeds": [{
            "title": "✅ Gator's restock watch is running",
            "description": f"Checked {len(products)} Pokémon products ({live} in stock). Nothing new since the last "
                           "check. It keeps checking every few minutes and posts here when something restocks.",
            "color": GREEN}]})
    for i in range(0, len(embeds), 10):          # Discord allows 10 embeds per message
        post({"username": "Gator's Restock Watch", "embeds": embeds[i:i + 10]})
        time.sleep(1)
    save_state(products)
    print(f"Checked {len(products)} products: {len(found)} restock/new.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
