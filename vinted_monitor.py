import os, time, sqlite3, requests, urllib.parse
from bs4 import BeautifulSoup

# ========= CONFIG UTILISATEUR =========
MODELS = ["11", "12", "13", "14", "15", "16"]
KEYWORDS = [
    "cassé", "hs", "vitre cassé", "vitre cassée", "écran cassé",
    "batterie morte", "carte mere hs", "carte mère hs",
    "bloqué icloud", "verrouillé icloud", "icloud bloqué"
]
PRICE_TO = 200
CHECK_INTERVAL_SEC = 60  # augmente à 90-120 si besoin

# Telegram via variables d'environnement (obligatoire)
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")
if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
    raise SystemExit("⚠️ Définis TELEGRAM_BOT_TOKEN et TELEGRAM_CHAT_ID dans les variables d’environnement.")

USER_AGENT = "Mozilla/5.0 (monitor-bot; contact: you@example.com)"

# ========= STORAGE (doublons) =========
conn = sqlite3.connect("seen_vinted.db")
cur = conn.cursor()
cur.execute("""CREATE TABLE IF NOT EXISTS seen(
  source TEXT, item_id TEXT, url TEXT, title TEXT, price TEXT, first_seen INTEGER,
  PRIMARY KEY(source, item_id)
)""")
conn.commit()

def notify(text: str):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "disable_web_page_preview": True}, timeout=10)
    except Exception as e:
        print("[ERR notify]", e)

def build_search_urls():
    base = "https://www.vinted.fr/catalog"
    urls = {}
    for m in MODELS:
        for kw in KEYWORDS:
            q = f"iphone {m} {kw}"
            params = {"search_text": q, "price_to": str(PRICE_TO), "order": "newest_first"}
            url = base + "?" + urllib.parse.urlencode(params, doseq=True)
            key = f"iphone{m}_{kw.replace(' ', '_')}"
            urls[key] = url
    return urls

def fetch(url: str) -> str:
    r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=15)
    r.raise_for_status()
    return r.text

def parse_vinted(html: str):
    soup = BeautifulSoup(html, "html.parser")
    items = []
    for a in soup.select("a[href*='/items/']"):
        url = a.get("href")
        if not url:
            continue
        if url.startswith("/"):
            url = "https://www.vinted.fr" + url
        try:
            item_id = url.split("/items/")[1].split("-")[0].split("?")[0]
        except Exception:
            continue
        title = a.get("title") or a.get_text(strip=True) or "(sans titre)"
        price = ""
        price_el = a.find_next(string=lambda s: isinstance(s, str) and "€" in s)
        if price_el:
            price = price_el.strip()
        items.append({"id": item_id, "title": title, "price": price, "url": url})
    return list({it["id"]: it for it in items}.values())

def already_seen(source: str, item_id: str) -> bool:
    cur.execute("SELECT 1 FROM seen WHERE source=? AND item_id=?", (source, item_id))
    return cur.fetchone() is not None

def mark_seen(source: str, it: dict):
    cur.execute(
        "INSERT OR IGNORE INTO seen(source, item_id, url, title, price, first_seen) "
        "VALUES (?,?,?,?,?,strftime('%s','now'))",
        (source, it["id"], it["url"], it.get("title",""), it.get("price",""))
    )
    conn.commit()

def run_once(SEARCHES: dict):
    for name, url in SEARCHES.items():
        try:
            html = fetch(url)
            for it in parse_vinted(html):
                if it["id"] and not already_seen(name, it["id"]):
                    mark_seen(name, it)
                    notify(f"🆕 {name}\n{it.get('title','(sans titre)')}\n{it.get('price','')}\n{it['url']}")
        except Exception as e:
            print(f"[ERR] {name}: {e}")

if __name__ == "__main__":
    SEARCHES = build_search_urls()
    print("Surveillance des recherches :")
    for k, u in SEARCHES.items():
        print("-", k, "=>", u)
    while True:
        run_once(SEARCHES)
        time.sleep(CHECK_INTERVAL_SEC)
