#!/usr/bin/env python3
"""Bot d'alertes Vinted -> Telegram.

Surveille les recherches listées dans searches.txt et envoie sur Telegram
chaque nouvelle annonce. Ne se connecte pas à votre compte Vinted (aucun
identifiant requis) et n'achète rien : il alerte seulement.

Usage :
    python bot.py          # un seul passage (pour GitHub Actions)
    python bot.py --loop   # tourne en continu (PC, Raspberry Pi, VPS, Termux)
"""
import json
import os
import re
import sys
import time
from urllib.parse import parse_qs, urlparse

import requests

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
SEEN_FILE = "seen.json"
SEARCHES_FILE = "searches.txt"
MAX_SEEN = 500          # nb d'IDs mémorisés par recherche
LOOP_DELAY = 60         # secondes entre deux passages en mode --loop
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


def load_searches():
    if not os.path.exists(SEARCHES_FILE):
        sys.exit(f"{SEARCHES_FILE} introuvable.")
    out = []
    with open(SEARCHES_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                out.append(line)
    if not out:
        sys.exit(f"Aucune recherche dans {SEARCHES_FILE}.")
    return out


def load_seen():
    if os.path.exists(SEEN_FILE):
        try:
            with open(SEEN_FILE, encoding="utf-8") as f:
                return json.load(f)
        except json.JSONDecodeError:
            return {}
    return {}


def save_seen(seen):
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(seen, f, indent=1)


def make_session(domain):
    s = requests.Session()
    s.headers.update({
        "User-Agent": UA,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
    })
    # La page d'accueil dépose les cookies de session nécessaires à l'API.
    s.get(f"https://{domain}/", timeout=20)
    return s


def api_params(url):
    """Convertit une URL de recherche Vinted (navigateur) en paramètres API."""
    parsed = urlparse(url)
    params = {"per_page": 30, "order": "newest_first"}
    m = re.search(r"/catalog/(\d+)", parsed.path)
    if m:
        params["catalog_ids"] = m.group(1)
    for key, values in parse_qs(parsed.query).items():
        key = key.replace("[]", "")
        if key == "catalog":
            key = "catalog_ids"
        if key in ("order", "search_text", "price_from", "price_to", "currency"):
            params[key] = values[0]
        else:
            params[key] = ",".join(values)
    return params


def fetch_items(session, url):
    domain = urlparse(url).netloc
    endpoint = f"https://{domain}/api/v2/catalog/items"

    try:
        r = session.get(
            endpoint,
            params=api_params(url),
            timeout=20,
        )
    except requests.RequestException as e:
        raise RuntimeError(f"Erreur réseau vers Vinted : {e}") from e

    if r.status_code == 404:
        raise RuntimeError(
            "L'endpoint de recherche Vinted renvoie HTTP 404. "
            f"URL appelée : {r.url}. "
            "Le chemin API peut être indisponible ou avoir changé."
        )

    if r.status_code in (401, 403, 429):
        raise RuntimeError(
            f"Vinted a refusé la requête (HTTP {r.status_code}). "
            "La requête peut nécessiter un accès autorisé."
        )

    r.raise_for_status()

    try:
        data = r.json()
    except ValueError as e:
        raise RuntimeError(
            "Vinted n'a pas renvoyé de JSON valide."
        ) from e

    if not isinstance(data, dict):
        raise RuntimeError("Réponse Vinted inattendue : objet JSON invalide.")

    return data.get("items", [])


def price_of(item):
    p = item.get("total_item_price") or item.get("price")
    if isinstance(p, dict):
        return f"{p.get('amount')} {p.get('currency_code', '€')}"
    return f"{p} {item.get('currency', '€')}"


def notify(item, search_url):
    caption = (
        f"<b>{item.get('title', 'Annonce')}</b>\n"
        f"💶 {price_of(item)}\n"
        f"🏷 {item.get('brand_title') or '-'}  |  📏 {item.get('size_title') or '-'}"
        f"  |  ✨ {item.get('status') or '-'}\n"
        f"{item.get('url', '')}"
    )
    photo = (item.get("photo") or {}).get("url")
    base = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
    if photo:
        r = requests.post(
            f"{base}/sendPhoto",
            data={"chat_id": TELEGRAM_CHAT_ID, "photo": photo,
                  "caption": caption, "parse_mode": "HTML"},
            timeout=20,
        )
        if r.ok:
            return
    requests.post(
        f"{base}/sendMessage",
        data={"chat_id": TELEGRAM_CHAT_ID, "text": caption, "parse_mode": "HTML"},
        timeout=20,
    ).raise_for_status()


def run_once(searches):
    seen = load_seen()
    sessions = {}
    errors = 0
    for url in searches:
        domain = urlparse(url).netloc
        try:
            if domain not in sessions:
                sessions[domain] = make_session(domain)
            items = fetch_items(sessions[domain], url)
        except Exception as e:  # noqa: BLE001
            print(f"[ERREUR] {url}\n  {e}")
            errors += 1
            continue

        known = set(seen.get(url, []))
        first_run = url not in seen
        new_items = [it for it in items if str(it["id"]) not in known]

        if first_run:
            print(f"[INIT] {url} : {len(items)} annonces mémorisées, pas de notification.")
        else:
            # du plus ancien au plus récent
            for it in reversed(new_items):
                try:
                    notify(it, url)
                    print(f"[ALERTE] {it.get('title')}")
                except Exception as e:  # noqa: BLE001
                    print(f"[ERREUR Telegram] {e}")
            if not new_items:
                print(f"[OK] {url} : rien de nouveau.")

        ids = [str(it["id"]) for it in items] + list(known)
        seen[url] = list(dict.fromkeys(ids))[:MAX_SEEN]
    save_seen(seen)
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
