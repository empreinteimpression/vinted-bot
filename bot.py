#!/usr/bin/env python3
"""Alertes Telegram à partir des pages publiques du catalogue Vinted."""

import html
import json
import os
import re
import sys
import time
import unicodedata
from urllib.parse import parse_qs, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

SEARCHES_FILE = "searches.txt"
SEEN_FILE = "seen.json"
MAX_SEEN = 500

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
}


def load_searches():
    if not os.path.isfile(SEARCHES_FILE):
        sys.exit(f"Fichier manquant : {SEARCHES_FILE}")

    with open(SEARCHES_FILE, encoding="utf-8") as f:
        urls = [
            line.strip()
            for line in f
            if line.strip() and not line.strip().startswith("#")
        ]

    if not urls:
        sys.exit("Aucune URL dans searches.txt.")

    for url in urls:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        if parsed.scheme != "https" or not (
            host == "vinted.fr" or host.endswith(".vinted.fr")
        ):
            sys.exit(f"URL non autorisée : {url}")

    return urls


def load_seen():
    try:
        with open(SEEN_FILE, encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except (json.JSONDecodeError, OSError) as exc:
        print(f"[ERREUR] Lecture de {SEEN_FILE} : {exc}")
        return {}


def save_seen(seen):
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(seen, f, ensure_ascii=False, indent=2)


def parse_item(card, page_url):
    link = card.select_one('a[href*="/items/"]')
    if not link:
        return None

    href = urljoin(page_url, link.get("href", ""))
    match = re.search(r"/items/(\d+)", urlparse(href).path)
    if not match:
        return None

    image = card.select_one("img")
    title = (
        link.get("title")
        or (image.get("alt") if image else "")
        or card.get_text(" ", strip=True)
        or "Annonce Vinted"
    )

    image_url = ""
    if image:
        image_url = image.get("src") or image.get("data-src") or ""

    return {
        "id": match.group(1),
        "title": title.strip(),
        "url": href,
        "photo": image_url,
        "details": card.get_text(" ", strip=True),
    }


def fetch_items(session, url):
    response = session.get(url, timeout=25)

    if response.status_code in (401, 403, 429):
        raise RuntimeError(
            f"Vinted a refusé la requête (HTTP {response.status_code}). "
            "Réessaie plus tard."
        )

    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")

    # Les identifiants des cartes peuvent varier selon la version du site.
    cards = soup.select('[data-testid^="product-item-id-"]')

    if not cards:
        cards = soup.select('a[href*="/items/"]')

    items = {}
    for card in cards:
        item = parse_item(card, url)
        if item:
            items[item["id"]] = item

    if not items:
        title = soup.title.get_text(" ", strip=True) if soup.title else "inconnu"
        low = title.lower()
        blocked = any(
            k in low
            for k in ("just a moment", "access denied", "captcha", "robot",
                      "attention required", "forbidden", "inconnu")
        )
        if "vinted" in low and not blocked:
            # Page de résultats normale mais vide : aucune annonce pour l'instant.
            print(f"[VIDE] Aucune annonce actuellement pour : {url}")
            return []
        raise RuntimeError(
            "Aucune annonce exploitable détectée dans le HTML reçu. "
            f"Titre de page : {title}. "
            "Le site peut nécessiter JavaScript ou avoir changé de structure."
        )

    return list(items.values())


def _norm(text):
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    return text.lower()


def keywords_of(url):
    """Mots de la recherche (search_text) qui doivent figurer dans l'annonce."""
    query = parse_qs(urlparse(url).query).get("search_text", [""])[0]
    return [w for w in re.findall(r"\w+", _norm(query)) if len(w) >= 2]


def matches(item, words):
    text = " ".join(re.findall(r"\w+", _norm(item["title"] + " " + item["details"])))
    return all(w in text for w in words)


def notify(item):
    api_url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

    title = html.escape(item["title"])
    url = html.escape(item["url"], quote=True)
    details = html.escape(item["details"][:500])

    message = (
        f"<b>{title}</b>\n"
        f"{details}\n"
        f'<a href="{url}">Voir sur Vinted</a>'
    )

    response = requests.post(
        api_url,
        data={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "HTML",
            "disable_web_page_preview": "false",
        },
        timeout=20,
    )
    response.raise_for_status()

    result = response.json()
    if not result.get("ok"):
        raise RuntimeError(f"Erreur Telegram : {result}")


def run_once(urls):
    seen = load_seen()
    errors = 0

    with requests.Session() as session:
        session.headers.update(HEADERS)

        for index, url in enumerate(urls):
            if index:
                time.sleep(2)

            try:
                items = fetch_items(session, url)
            except (requests.RequestException, RuntimeError) as exc:
                print(f"[ERREUR] {url}\n  {exc}")
                errors += 1
                continue

            first_run = url not in seen
            known = set(str(x) for x in seen.get(url, []))

            if first_run:
                print(
                    f"[INIT] {len(items)} annonces mémorisées "
                    "sans notification initiale."
                )
            else:
                words = keywords_of(url)
                new_items = [
                    item for item in items
                    if item["id"] not in known and matches(item, words)
                ]

                for item in reversed(new_items):
                    try:
                        notify(item)
                        print(f"[ALERTE] {item['title']} - {item['url']}")
                    except Exception as exc:
                        print(f"[ERREUR Telegram] {exc}")
                        errors += 1

                if not new_items:
                    print(f"[OK] Aucun nouvel article : {url}")

            # N'enregistre les identifiants que si l'extraction a réussi.
            ids = [item["id"] for item in items]
            ids.extend(str(x) for x in seen.get(url, []))
            seen[url] = list(dict.fromkeys(ids))[:MAX_SEEN]
            save_seen(seen)

    return errors


def main():
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        sys.exit("Secrets TELEGRAM_TOKEN et TELEGRAM_CHAT_ID requis.")

    urls = load_searches()
    errors = run_once(urls)
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
