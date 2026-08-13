#!/usr/bin/env python3
"""
watch_carvago.py

Sleduje nové inzeráty Audi A5 na Carvago.com podle zadaného filtrovaného URL.
Filtry uplatněné přímo v URL: automat, cena 400 000–1 000 000 Kč, registrace od 2024.
Skript navíc ověřuje nájezd (<=100 000 km) a stáří (<=4 roky) jako pojistku.

Bez omezení na zemi původu vozu (Německo, Nizozemsko, Belgie apod. jsou OK).

Nové nalezené inzeráty se nahlásí vytvořením GitHub issue (které se následně
automaticky zavře) -> to odešle e-mail díky GitHub notifikacím, stejně jako
u watch_cars.py / watch_tipcars.py.
"""

import os
import re
import json
import sys
from datetime import datetime, date
import requests
from bs4 import BeautifulSoup

SEARCH_URL = (
    "https://carvago.com/cs/auta/audi/a5/prevodovka-automat"
    "?price-from=400000&price-to=1000000&registration-date-from=2024"
    "&sort=price&direction=asc"
)

STATE_FILE = "carvago_state.json"

MAX_PRICE_CZK = 1_400_000
MAX_AGE_YEARS = 4
MAX_MILEAGE_KM = 100_000

GITHUB_REPO = os.environ.get("GITHUB_REPOSITORY")  # např. "user/cinemacity-watchdog"
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"known_ids": []}


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def parse_number(text):
    """'10 440 km' / '985 490 Kč' -> 10440 / 985490 (int)."""
    digits = re.sub(r"[^\d]", "", text or "")
    return int(digits) if digits else None


def parse_first_registration(text):
    """'5/2025' -> date(2025, 5, 1). Pokud jen rok, vezme leden."""
    if not text:
        return None
    text = text.strip()
    m = re.match(r"(\d{1,2})/(\d{4})", text)
    if m:
        month, year = int(m.group(1)), int(m.group(2))
        return date(year, month, 1)
    m = re.match(r"(\d{4})", text)
    if m:
        return date(int(m.group(1)), 1, 1)
    return None


def age_years(reg_date):
    if not reg_date:
        return None
    today = date.today()
    years = today.year - reg_date.year - (
        (today.month, today.day) < (reg_date.month, reg_date.day)
    )
    return years


def fetch_listings():
    resp = requests.get(SEARCH_URL, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    listings = []
    cards = soup.find_all("a", attrs={"data-car-id": True})

    for card in cards:
        car_id = card.get("data-car-id")
        href = card.get("href")
        if not car_id or not href:
            continue

        url = "https://carvago.com" + href if href.startswith("/") else href

        title_tag = card.find("h2")
        title = title_tag.get_text(strip=True) if title_tag else "Audi A5"

        mileage_div = card.find("div", title="Najeto")
        mileage_km = None
        if mileage_div:
            val_div = mileage_div.find("div", class_="css-1vdwlsb")
            if val_div:
                mileage_km = parse_number(val_div.get_text())

        reg_div = card.find("div", title="První registrace")
        reg_date = None
        if reg_div:
            val_div = reg_div.find("div", class_="css-1vdwlsb")
            if val_div:
                reg_date = parse_first_registration(val_div.get_text())

        transmission_div = card.find("div", title="Převodovka")
        transmission = None
        if transmission_div:
            val_div = transmission_div.find("div", class_="css-1vdwlsb")
            if val_div:
                transmission = val_div.get_text(strip=True)

        price_tag = card.find("p", class_="css-1qi6om6")
        price_czk = parse_number(price_tag.get_text()) if price_tag else None

        location_div = card.find("div", class_="css-ge87hl")
        location = location_div.get_text(strip=True) if location_div else None

        listings.append({
            "car_id": car_id,
            "title": title,
            "url": url,
            "mileage_km": mileage_km,
            "first_registration": reg_date.isoformat() if reg_date else None,
            "transmission": transmission,
            "price_czk": price_czk,
            "location": location,
        })

    return listings


def passes_filters(listing):
    price = listing.get("price_czk")
    if price is None or price > MAX_PRICE_CZK:
        return False

    mileage = listing.get("mileage_km")
    if mileage is None or mileage > MAX_MILEAGE_KM:
        return False

    reg = listing.get("first_registration")
    if reg:
        reg_date = date.fromisoformat(reg)
        if age_years(reg_date) is not None and age_years(reg_date) > MAX_AGE_YEARS:
            return False

    # Bez filtru na zemi - Německo, Nizozemsko atd. jsou v pořádku.
    return True


def create_github_issue(listing):
    if not GITHUB_REPO or not GITHUB_TOKEN:
        print("GITHUB_REPOSITORY / GITHUB_TOKEN nejsou nastaveny, issue nevytvořeno.")
        return

    title = f"Nová nabídka: {listing['title']} – {listing['price_czk']} Kč"
    body_lines = [
        f"**{listing['title']}**",
        "",
        f"- Cena: {listing['price_czk']} Kč",
        f"- Nájezd: {listing['mileage_km']} km",
        f"- První registrace: {listing['first_registration']}",
        f"- Převodovka: {listing['transmission']}",
        f"- Lokace: {listing['location']}",
        "",
        f"[Zobrazit inzerát]({listing['url']})",
    ]
    body = "\n".join(body_lines)

    api_url = f"https://api.github.com/repos/{GITHUB_REPO}/issues"
    resp = requests.post(
        api_url,
        headers={
            "Authorization": f"Bearer {GITHUB_TOKEN}",
            "Accept": "application/vnd.github+json",
        },
        json={"title": title, "body": body, "labels": ["carvago", "audi-a5"]},
        timeout=30,
    )
    resp.raise_for_status()
    issue = resp.json()
    issue_number = issue["number"]
    print(f"Vytvořeno issue #{issue_number} pro {listing['car_id']}")

    # Zavřít issue - notifikace/e-mail už proběhla při vytvoření.
    close_url = f"https://api.github.com/repos/{GITHUB_REPO}/issues/{issue_number}"
    requests.patch(
        close_url,
        headers={
            "Authorization": f"Bearer {GITHUB_TOKEN}",
            "Accept": "application/vnd.github+json",
        },
        json={"state": "closed"},
        timeout=30,
    )


def main():
    force_report = os.environ.get("FORCE_REPORT", "false").lower() == "true"

    state = load_state()
    known_ids = set(state.get("known_ids", []))

    try:
        listings = fetch_listings()
    except Exception as e:
        print(f"Chyba při stahování inzerátů: {e}")
        sys.exit(1)

    print(f"Nalezeno {len(listings)} inzerátů na stránce.")

    matching = [l for l in listings if passes_filters(l)]
    print(f"Po filtrech (cena/km/stáří) vyhovuje {len(matching)} inzerátů.")

    new_listings = [l for l in matching if l["car_id"] not in known_ids]

    to_report = matching if force_report else new_listings

    if not to_report:
        print("Žádné nové vyhovující inzeráty.")
    else:
        for listing in to_report:
            print(f"Nahlašuji: {listing['title']} ({listing['car_id']}) - {listing['url']}")
            create_github_issue(listing)

    # Aktualizovat stav vždy podle toho, co skutečně vyhovuje filtrům,
    # aby se známé inzeráty neposílaly opakovaně.
    known_ids.update(l["car_id"] for l in matching)
    state["known_ids"] = sorted(known_ids)
    save_state(state)

    print("Hotovo.")


if __name__ == "__main__":
    main()
