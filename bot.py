
#!/usr/bin/env python3
"""Alertes Telegram pour les nouvelles annonces du catalogue public Vinted."""

import html
import json
import os
import re
import sys
import time
from urllib.parse import urlparse, urljoin

import requests
from bs4 import BeautifulSoup

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

SEEN_FILE = "seen.json"
SEARCHES_FILE = "searches.txt"
MAX_SEEN = 500
LOOP_DELAY = 60
REQUEST_DELAY = 2

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


def load_searches():
    if not os.path.exists(SEARCHES_FILE):
        sys.exit(f"{SEARCHES_FILE} introuvable.")

    with open(SEARCHES_FILE, encoding="utf-8") as f:
        searches = [
            line.strip()
            for line in f
            if line.strip() and not line.strip().startswith("#")
        ]

    if not searches:
        sys.exit(f"Aucune recherche dans {SEARCHES_FILE}.")

    for url in searches:
        parsed = urlparse(url)
        if parsed.scheme != "https" or not (
            parsed.hostname or ""
        ).endswith("vinted.fr"):
            sys.exit(f"URL de recherche non autorisée : {url}")

    return searches


def load_seen():
    if os.path.exists(SEEN_FILE):
        try:
            with open(SEEN_FILE, encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, OSError):
            print("[AVERTISSEMENT] seen.json illisible ; initialisation vide.")
    return {}


def save_seen(seen):
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(seen, f, ensure_ascii=False, indent=2)


def make_session():
    session = requests.Session()
    session.headers.update({
        "User-Agent": UA,
        "Accept": (
            "text/html,application/xhtml+xml,"
            "application/xml;q=0.9,*/*;q=0.8"
        ),
        "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.7",
    })
    return session


def text_of(element):
    return element.get_text(" ", strip=True) if element else ""


def extract_id(card, href):
    testid = card.get("data-testid", "")
    match = re.search(r"product-item-id-(\d+)", testid)
    if match:
        return match.group(1)

    match = re.search(r"/items/(\d+)", href or "")
    if match:
        return match.group(1)

    return ""


def parse_card(card, base_url):
    link = card.select_one(
        'a[data-testid$="--overlay-link"]'
    )

    if not link:
        link = card.select_one('a[href*="/items/"]')

    image = card.select_one(
        'img[data-testid$="--image--img"]'
    )
    if not image:
        image = card.select_one("img")

    brand_el = card.select_one(
        'p[data-testid$="--description-title"]'
    )
    subtitle_el = card.select_one(
        'p[data-testid$="--description-subtitle"]'
    )
    price_el = card.select_one(
        'p[data-testid$="--price-text"]'
    )

    href = link.get("href", "") if link else ""
    if href:
        href = urljoin(base_url, href)

    item_id = extract_id(card, href)
    if not item_id:
        return None

    brand = text_of(brand_el)
    subtitle = text_of(subtitle_el)
    price_text = text_of(price_el)

    title = ""
    if link:
        title = link.get("title", "").strip()
    if not title and image:
        title = image.get("alt", "").strip()
    if not title:
        title = " ".join(x for x in (brand, subtitle) if x)
    if not title:
        title = f"Annonce Vinted {item_id}"

    image_url = ""
    if image:
        image_url = (
            image.get("src")
            or image.get("data-src")
            or ""
        )
        if not image_url:
            srcset = image.get("srcset", "")
            if srcset:
                image_url = srcset.split(",")[0].strip().split(" ")[0]

    # Ne pas créer de fausses annonces sans prix ni lien.
    if not href:
        return None

    return {
        "id": item_id,
        "title": title,
        "url": href,
        "brand_title": brand,
        "size_title": subtitle,
        "status": "",
        "price": price_text or "Prix non détecté",
        "photo": {"url": image_url} if image_url else {},
    }


def fetch_items(session, search_url):
    response = session.get(search_url, timeout=(10, 25))

    if response.status_code in (401, 403, 429):
        raise RuntimeError(
            f"Vinted a refusé la page (HTTP {response.status_code}). "
            "Arrêt de cette recherche ; réessaie plus tard."
        )

    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")

    # Sélecteurs des cartes d'annonces du catalogue.
    cards = soup.select(
        'div[data-testid^="product-item-id-"]'
    )

    if not cards:
        # Une page de blocage ou un changement de structure
        # ne doit pas être interprété comme zéro annonce.
        title = text_of(soup.title)
        raise RuntimeError(
            "Aucune carte d'annonce détectée dans le HTML. "
            f"Titre de la page : {title or 'inconnu'}. "
            "Le catalogue peut nécessiter JavaScript ou sa structure "
            "HTML a changé. Aucune annonce ne sera marquée comme vue."
        )

    items = []
    for card in cards:
        item = parse_card(card, search_url)
        if item:
            items.append(item)

    if not items:
        raise RuntimeError(
            f"{len(cards)} cartes HTML trouvées, mais aucun identifiant "
            "et lien d'annonce exploitable. Le parseur doit être adapté."
        )

    return items


def notify(item):
    base = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

    # Échappement HTML pour éviter qu'un titre casse le message Telegram.
    title = html.escape(str(item.get("title") or "Annonce"))
    brand = html.escape(str(item.get("brand_title") or "-"))
    size = html.escape(str(item.get("size_title") or "-"))
    price = html.escape(str(item.get("price") or "-"))
    url = html.escape(str(item.get("url") or ""), quote=True)

    caption = (
        f"<b>{title}</b>\n"
        f"💶 {price}\n"
        f"🏷 {brand} | 📏 {size}\n"
        f'<a href="{url}">Voir l’annonce</a>'
    )

    photo = (item.get("photo") or {}).get("url")

    if photo:
        try:
            r = requests.post(
                f"{base}/sendPhoto",
                data={
                    "chat_id": TELEGRAM_CHAT_ID,
                    "photo": photo,
                    "caption": caption,
                    "parse_mode": "HTML",
                },
                timeout=20,
            )
            if r.ok and r.json().get("ok"):
                return
        except (requests.RequestException, ValueError):
            pass

    r = requests.post(
        f"{base}/sendMessage",
        data={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": caption,
            "parse_mode": "HTML",
            "disable_web_page_preview": "true",
        },
        timeout=20,
    )
    r.raise_for_status()

    if not r.json().get("ok"):
        raise RuntimeError(f"Telegram a refusé le message : {r.text[:300]}")


def run_once(searches):
    seen = load_seen()
    session = make_session()
    errors = 0

    try:
        for index, url in enumerate(searches):
            if index:
                time.sleep(REQUEST_DELAY)

            try:
                items = fetch_items(session, url)
            except Exception as e:
                print(f"[ERREUR] {url}\n  {e}")
                errors += 1
                continue

            known = set(str(x) for x in seen.get(url, []))
            first_run = url not in seen

            new_items = [
                item for item in items
                if str(item["id"]) not in known
            ]

            if first_run:
                print(
                    f"[INIT] {url} : {len(items)} annonces mémorisées, "
                    "aucune notification initiale."
                )
            else:
                for item in reversed(new_items):
                    try:
                        notify(item)
                        print(
                            f"[ALERTE] {item.get('title')} "
                            f"({item.get('url')})"
                        )
                    except Exception as e:
                        print(f"[ERREUR Telegram] {e}")
                        errors += 1

                if not new_items:
                    print(f"[OK] {url} : rien de nouveau.")

            # N'enregistre la recherche qu'après une extraction valide.
            # Les annonces les plus récentes restent en tête.
            old_ids = seen.get(url, [])
            ids = [str(item["id"]) for item in items]
            ids.extend(str(x) for x in old_ids)
            seen[url] = list(dict.fromkeys(ids))[:MAX_SEEN]

            save_seen(seen)

    finally:
        session.close()

    return errors


def main():
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        sys.exit("Définissez TELEGRAM_TOKEN et TELEGRAM_CHAT_ID.")

    searches = load_searches()

    if "--loop" in sys.argv:
        while True:
            run_once(searches)
            time.sleep(LOOP_DELAY)
    else:
        sys.exit(1 if run_once(searches) else 0)


if __name__ == "__main__":
    main()
