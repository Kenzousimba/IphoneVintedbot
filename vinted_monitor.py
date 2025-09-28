# vinted_monitor.py
# Alerte Telegram pour annonces Vinted iPhone (11→16) HS/cassés/iCloud bloqué ≤ 200 €
# Filtre les accessoires (coques/câbles/chargeurs...) pour ne garder que les téléphones.

import os
import time
import sqlite3
import requests
import urllib.parse
from bs4 import BeautifulSoup

# ========= CONFIG UTILISATEUR =========
MODELS = ["11", "12", "13", "14", "15", "16"]

# Termes qui indiquent qu'on cherche des téléphones abîmés / bloqués
KEYWORDS = [
    "cassé", "cassée",
    "hs",
    "vitre cassé", "vitre cassée",
    "écran cassé", "ecran cassé", "ecran casse", "écran casse",
    "batterie morte", "batterie hs",
    "carte mere hs", "carte mère hs", "carte mere", "carte mère",
    "bloqué icloud", "icloud bloqué",
    "verrouillé icloud", "icloud verrouillé",
]

# ❌ Mots à EXCLURE (accessoires)
EXCLUDE_KEYWORDS = [
    "coque", "housse", "étui", "etui", "flip", "folio",
    "film", "verre trempé", "verre trempe", "protect", "protection écran", "protection ecran",
    "camera lens", "objectif caméra", "objectif camera", "lentille",
    "câble", "cable", "cordon", "chargeur", "chargeur secteur", "magsafe",
    "powerbank", "batterie externe", "adaptateur", "adaptator", "usb", "prise", "dock", "station",
    "support", "trépied", "trepied", "anneau", "bague", "ring", "sticker", "skin",
    "airpods", "écouteurs", "ecouteurs", "earpods", "casque",
    "verre", "vitre de protection",
    "pour iphone", "compatible iphone", "iphone 11/12/13/14/15/16",
    "coqu", "case", "cover"
]

PRICE_TO = 200
CHECK_INTERVAL_SEC = 90  # 60–120 recommandé pour être raisonnable

# Telegram via variables d'environnement (OBLIGATOIRE)
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
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

# ========= UTILS =========
def notify(text: str):
    """Envoie un message Telegram."""
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "disable_web_page_preview": True}, timeout=10)
    except Exception as e:
        print("[ERR notify]", e)

def normalize(s: str) -> str:
    return (s or "").strip().lower()

def is_phone_title(title: str) -> bool:
    """
    Retourne True si le titre ressemble à un téléphone (pas un accessoire) :
      - contient 'iphone'
      - ne contient aucun mot de EXCLUDE_KEYWORDS
      - mentionne un modèle (11..16) éventuellement avec variantes (pro/pro max/plus/max)
    """
    t = normalize(title)

    # exclure les accessoires
    if any(kw in t for kw in EXCLUDE_KEYWORDS):
        return False

    # doit contenir "iphone"
    if "iphone" not in t:
        return False

    # doit contenir au moins un numéro de modèle (avec variantes usuelles)
    candidates = set()
    for m in MODELS:
        candidates.update({
            f" {m}", f"{m} ", f"{m}pro", f"{m} pro", f"{m} pro max", f"{m} promax",
            f"{m} plus", f"{m} max", f"iphone {m}", f"iphone{m}",
        })
    return any(tok in t for tok in candidates)

def build_search_urls():
    """
    Construit des URLs Vinted pour chaque combinaison (modèle × mot-clé).
    On ajoute aussi des 'mots négatifs' dans la requête pour filtrer en amont.
    """
    base = "https://www.vinted.fr/catalog"
    urls = {}

    # Mots négatifs (parfois ignorés par Vinted, mais ça aide quand c'est pris en compte)
    neg = "-coque -housse -etui -film -verre -protection -cable -câble -chargeur -magsafe " \
          "-airpods -écouteurs -ecouteurs -adaptateur -usb -support -dock -skin -sticker -case -cover"

    for m in MODELS:
        for kw in KEYWORDS:
            q = f"iphone {m} {kw} {neg}"
            params = {"search_text": q, "price_to": str(PRICE_TO), "order": "newest_first"}
            url = base + "?" + urllib.parse.urlencode(params, doseq=True)
            key = f"iphone{m}_{kw.replace(' ', '_')}"
            urls[key] = url
    return urls

def fetch(url: str) -> str:
    r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=20)
    r.raise_for_status()
    return r.text

def parse_vinted(html: str):
    """
    Parse tolérant : on récupère les liens contenant /items/ + titre + tentative de prix.
    Le markup Vinted change régulièrement, donc on reste simple et robuste.
    """
    soup = BeautifulSoup(html, "html.parser")
    items = []
    for a in soup.select("a[href*='/items/']"):
        url = a.get("href")
        if not url:
            continue
        if url.startswith("/"):
            url = "https://www.vinted.fr" + url
        # ID sous la forme /items/123456789-...
        try:
            item_id = url.split("/items/")[1].split("-")[0].split("?")[0]
        except Exception:
            continue

        title = a.get("title") or a.get_text(strip=True) or ""
        # tentative de récupération du prix à proximité
        price = ""
        price_el = a.find_next(string=lambda s: isinstance(s, str) and "€" in s)
        if price_el:
            price = price_el.strip()

        items.append({"id": item_id, "title": title, "price": price, "url": url})

    # dédoublonnage par id
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
                if not it["id"]:
                    continue
                # ⚠️ Filtrage accessoires / validation iPhone
                if not is_phone_title(it.get("title", "")):
                    continue
                if not already_seen(name, it["id"]):
                    mark_seen(name, it)
                    notify(f"🆕 {name}\n{it.get('title','(sans titre)')}\n{it.get('price','')}\n{it['url']}")
        except Exception as e:
            print(f"[ERR] {name}: {e}")

# ========= MAIN =========
if __name__ == "__main__":
    SEARCHES = build_search_urls()
    print("Surveillance des recherches :")
    for k, u in SEARCHES.items():
        print("-", k, "=>", u)
    while True:
        run_once(SEARCHES)
        time.sleep(CHECK_INTERVAL_SEC)
