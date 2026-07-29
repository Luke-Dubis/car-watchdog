"""
watch_tipcars.py - Monitoring novych inzeratu Audi/Mercedes-Benz/BMW na tipcars.com
Filtry: cena do 1 200 000 Kc, stari max 4 roky, najezd max 100 000 km, automat.
Tipcars nema API, proto se cte primo HTML stranky (tzv. scraping).
"""

import json
import os
import re
import sys
import requests
from datetime import datetime
from bs4 import BeautifulSoup

STATE_FILE = "state_tipcars.json"
CURRENT_YEAR = datetime.now().year
YEAR_FROM = CURRENT_YEAR - 4

# URL vzorec zjisteny primo na tipcars.com (rok, cena, km, vybava = automat)
SEARCH_URLS = {
    "audi": f"https://www.tipcars.com/audi?{YEAR_FROM}-{CURRENT_YEAR}=&-1200000kc=&-100000km=&vybava=aut-prevodovka",
    "mercedes-benz": f"https://www.tipcars.com/mercedes-benz?{YEAR_FROM}-{CURRENT_YEAR}=&-1200000kc=&-100000km=&vybava=aut-prevodovka",
    "bmw": f"https://www.tipcars.com/bmw?{YEAR_FROM}-{CURRENT_YEAR}=&-1200000kc=&-100000km=&vybava=aut-prevodovka",
}

HEADERS = {
    "accept": "text/html",
    "accept-language": "cs",
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
}


def parse_number(text: str) -> int:
    """Vytahne cislo z textu jako '1 049 900 Kc' nebo '55 000 km'."""
    digits = re.sub(r"[^\d]", "", text or "")
    return int(digits) if digits else 0


def fetch_listings(brand: str) -> list[dict]:
    url = SEARCH_URLS[brand]
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    items = []
    # Kazda karta auta ma odkaz s data-offer-listing-id-param
    for link in soup.select("a[data-offer-listing-id-param]"):
        item_id = link.get("data-offer-listing-id-param")
        href = link.get("data-offer-listing-url-param") or link.get("href")
        if not item_id or not href:
            continue

        # Card je rodicovsky blok obsahujici i cenu a detail-boxy
        card = link
        for _ in range(6):
            if card.parent is None:
                break
            card = card.parent
            if card.select_one(".advertisement-name__price") and card.select_one(".advertisement-boxes"):
                break

        title_tag = link.select_one("h3")
        subtitle_tag = link.parent.select_one("p.text-M") if link.parent else None
        price_tag = card.select_one(".advertisement-name__price h3")

        year_tag = card.select_one('.detail-box-S[title*="Rok výroby"] .detail-box-S__text')
        tachometer_tag = card.select_one('.detail-box-S[title="Tachometr"] .detail-box-S__text')
        gearbox_tag = card.select_one('.detail-box-S[title="Převodovka"] .detail-box-S__text')

        items.append({
            "id": item_id,
            "url": "https://www.tipcars.com" + href if href.startswith("/") else href,
            "title": (title_tag.get_text(strip=True) if title_tag else "") + " " +
                     (subtitle_tag.get_text(strip=True) if subtitle_tag else ""),
            "price": parse_number(price_tag.get_text() if price_tag else ""),
            "year": parse_number(year_tag.get_text() if year_tag else ""),
            "tachometer": parse_number(tachometer_tag.get_text() if tachometer_tag else ""),
            "gearbox": gearbox_tag.get_text(strip=True) if gearbox_tag else "",
        })

    return items


def passes_filters(item: dict) -> bool:
    if item["price"] <= 0 or item["price"] > 1_200_000:
        return False
    if item["tachometer"] <= 0 or item["tachometer"] > 100_000:
        return False
    if item["year"] <= 0 or item["year"] < YEAR_FROM:
        return False
    if "automat" not in item["gearbox"].lower():
        return False
    return True


def load_state() -> set:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    return set()


def save_state(seen_ids: set) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(seen_ids), f, ensure_ascii=False, indent=2)


def create_github_issue(title: str, body: str) -> None:
    repo = os.environ["GITHUB_REPOSITORY"]
    token = os.environ["GITHUB_TOKEN"]
    url = f"https://api.github.com/repos/{repo}/issues"
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
    }
    resp = requests.post(url, headers=headers, json={"title": title, "body": body})
    resp.raise_for_status()
    issue_number = resp.json()["number"]

    close_url = f"https://api.github.com/repos/{repo}/issues/{issue_number}"
    requests.patch(close_url, headers=headers, json={"state": "closed"})


def main():
    force_report = os.environ.get("FORCE_REPORT", "false").lower() == "true"
    seen_ids = load_state()
    new_items = []
    total_checked = 0
    total_passed = 0

    for brand in SEARCH_URLS:
        try:
            listings = fetch_listings(brand)
        except Exception as e:
            print(f"Chyba pri stahovani {brand}: {e}", file=sys.stderr)
            continue

        total_checked += len(listings)
        print(f"{brand}: stazeno {len(listings)} inzeratu")

        for item in listings:
            if not passes_filters(item):
                continue
            total_passed += 1
            item_id = f"tipcars-{item['id']}"
            if item_id not in seen_ids or force_report:
                new_items.append(item)
            seen_ids.add(item_id)

    print(f"Celkem stazeno: {total_checked}, splnilo filtry: {total_passed}")

    if new_items:
        lines = []
        for item in new_items:
            lines.append(
                f"- **{item['title'].strip()}** ({item['year']}) - "
                f"{item['price']:,} Kc, {item['tachometer']:,} km\n  {item['url']}"
            )
        body = "\n".join(lines)
        create_github_issue(
            title=f"[TipCars] Nove inzeraty: {len(new_items)} vozu",
            body=body,
        )
        print(f"Nahlaseno {len(new_items)} novych inzeratu.")
    else:
        print("Zadne nove inzeraty nesplnujici filtry.")

    save_state(seen_ids)


if __name__ == "__main__":
    main()
