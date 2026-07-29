"""
watch_cars.py - Monitoring novych inzeratu Audi/Mercedes-Benz/BMW na sauto.cz
Filtry: cena do 1 200 000 Kc, stari max 4 roky, najezd max 100 000 km,
automaticka prevodovka, karoserie sedan/kombi/kupe/liftback/hatchback (bez SUV/dodavek).
"""

import json
import os
import sys
import time
import requests
from datetime import datetime

STATE_FILE = "state_sauto.json"
SEARCH_URL = "https://www.sauto.cz/api/v1/items/search"
DETAIL_URL = "https://www.sauto.cz/api/v1/items/{}"

CURRENT_YEAR = datetime.now().year
YEAR_FROM = CURRENT_YEAR - 4

BRANDS = ["audi", "mercedes-benz", "bmw"]

# Karoserie, ktere chceme (vylucujeme SUV, dodavky, MPV, pickupy, off-road)
ALLOWED_BODY_TYPES = {"sedan", "kombi", "kupe", "liftback", "hatchback"}

BASE_PARAMS = {
    "category_id": 838,
    "condition_seo": "ojete",
    "operating_lease": "false",
    "price_to": 1_200_000,
    "limit": 100,
    "offset": 0,
}

HEADERS = {
    "accept": "application/json",
    "accept-language": "cs",
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
}


def fetch_listings(brand: str) -> list[dict]:
    params = dict(BASE_PARAMS)
    params["manufacturer_model_seo"] = brand
    resp = requests.get(SEARCH_URL, params=params, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    return data.get("results", [])


def fetch_body_type(item_id: int) -> str:
    """Vrati seo_name karoserie (napr. 'sedan', 'suv') pro dane auto."""
    try:
        resp = requests.get(DETAIL_URL.format(item_id), headers=HEADERS, timeout=30)
        resp.raise_for_status()
        data = resp.json().get("result", {})
        return (data.get("vehicle_body_cb") or {}).get("seo_name", "")
    except Exception as e:
        print(f"Chyba pri detailu {item_id}: {e}", file=sys.stderr)
        return ""


def passes_basic_filters(item: dict) -> bool:
    """Levne filtry, overitelne bez extra dotazu."""
    price = item.get("price") or 0
    tachometer = item.get("tachometer")
    manufacturing_date = item.get("manufacturing_date")
    gearbox = (item.get("gearbox_cb") or {}).get("seo_name", "")

    if price <= 0 or price > BASE_PARAMS["price_to"]:
        return False
    if tachometer is None or tachometer > 100_000:
        return False
    if not manufacturing_date:
        return False
    year = int(manufacturing_date[:4])
    if year < YEAR_FROM:
        return False
    if gearbox != "automaticka":
        return False

    return True


def build_url(item: dict) -> str:
    brand = (item.get("manufacturer_cb") or {}).get("seo_name", "")
    model = (item.get("model_cb") or {}).get("seo_name", "")
    item_id = item.get("id")
    return f"https://www.sauto.cz/osobni/detail/{brand}/{model}/{item_id}"


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
    total_basic_passed = 0
    total_body_passed = 0

    for brand in BRANDS:
        try:
            listings = fetch_listings(brand)
        except Exception as e:
            print(f"Chyba pri stahovani {brand}: {e}", file=sys.stderr)
            continue

        total_checked += len(listings)
        print(f"{brand}: stazeno {len(listings)} inzeratu")

        for item in listings:
            if not passes_basic_filters(item):
                continue
            total_basic_passed += 1

            item_id = str(item.get("id"))

            # Detail (karoserie) potrebujeme jen pro auta, ktera uz projdou zakladnimi filtry
            body_type = fetch_body_type(item.get("id"))
            time.sleep(0.2)  # slusne tempo dotazu, nezatezujeme server

            if body_type not in ALLOWED_BODY_TYPES:
                continue
            total_body_passed += 1

            if item_id not in seen_ids or force_report:
                new_items.append(item)
            seen_ids.add(item_id)

    print(
        f"Celkem stazeno: {total_checked}, "
        f"zakladni filtry: {total_basic_passed}, "
        f"karoserie OK: {total_body_passed}"
    )

    if new_items:
        lines = []
        for item in new_items:
            make = (item.get("manufacturer_cb") or {}).get("name", "")
            model = (item.get("model_cb") or {}).get("name", "")
            year = (item.get("manufacturing_date") or "")[:4]
            price = item.get("price")
            tachometer = item.get("tachometer")
            url = build_url(item)
            lines.append(
                f"- **{make} {model}** ({year}) - {price:,} Kc, {tachometer:,} km\n  {url}"
            )

        body = "\n".join(lines)
        create_github_issue(
            title=f"Nove inzeraty: {len(new_items)} vozu",
            body=body,
        )
        print(f"Nahlaseno {len(new_items)} novych inzeratu.")
    else:
        print("Zadne nove inzeraty nesplnujici filtry.")

    save_state(seen_ids)


if __name__ == "__main__":
    main()
