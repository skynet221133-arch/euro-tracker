#!/usr/bin/env python3
"""Tracker dużych obniżek cen w Euro RTV AGD (euro.com.pl) z powiadomieniami na Telegram.

Uruchomienie:
    python tracker.py            # normalny przebieg
    python tracker.py --dump     # zapisz HTML pierwszej strony każdego URL do debug/ (do diagnozy)
    python tracker.py --selftest # test parsera na przykładowym fragmencie (bez internetu)

Zmienne środowiskowe:
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID  - dane bota (wymagane przy normalnym przebiegu)
    MIN_DISCOUNT   - minimalna obniżka w % (domyślnie 70)
    MIN_PRICE      - ignoruj produkty tańsze niż X zł (domyślnie 0)
    MAX_PAGES      - maks. liczba stron na jeden URL (domyślnie 30)
"""
import json
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

BASE = "https://www.euro.com.pl"
URLS_FILE = Path("urls.txt")
STATE_FILE = Path("state/seen.json")
DEBUG_DIR = Path("debug")

MIN_DISCOUNT = float(os.getenv("MIN_DISCOUNT", "70"))
MIN_PRICE = float(os.getenv("MIN_PRICE", "0"))
MAX_PAGES = int(os.getenv("MAX_PAGES", "30"))
DELAY = 1.5  # sekundy między zapytaniami - bądźmy grzeczni dla serwera

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "pl-PL,pl;q=0.9",
}

PRICE_RE = r"(\d{1,3}(?:[\s\u00a0\.]\d{3})*(?:,\d{1,2})?|\d+(?:,\d{1,2})?)\s*zł"


def to_float(s: str) -> float:
    s = re.sub(r"[\s\u00a0\.]", "", s).replace(",", ".")
    return float(s)


def first_price(text: str):
    m = re.search(PRICE_RE, text)
    return to_float(m.group(1)) if m else None


def price_after(label: str, text: str):
    m = re.search(label + r"\s*:?\s*" + PRICE_RE, text, re.I)
    return to_float(m.group(1)) if m else None


def parse_products(html: str, page_url: str = BASE):
    """Heurystyczny parser: szuka najmniejszych bloków z linkiem, ceną i ceną referencyjną.

    Nie opiera się na nazwach klas CSS (które często się zmieniają), tylko na tekstach
    "Przed obniżką" / "Najniższa cena" wymaganych przez dyrektywę Omnibus.
    """
    soup = BeautifulSoup(html, "html.parser")
    for t in soup(["script", "style", "noscript"]):
        t.decompose()

    def is_card(tag):
        txt = tag.get_text(" ", strip=True)
        return ("Przed obniżką" in txt or "Najniższa cena" in txt) and tag.find("a", href=True)

    cards = []
    for tag in soup.find_all(["article", "li", "div", "section"]):
        if not is_card(tag):
            continue
        # tylko najgłębszy blok - jeśli któreś dziecko też jest kartą, pomijamy rodzica
        if any(is_card(ch) for ch in tag.find_all(["article", "li", "div", "section"])):
            continue
        cards.append(tag)

    products, seen_urls = [], set()
    for card in cards:
        text = card.get_text(" ", strip=True)
        link = None
        for a in card.find_all("a", href=True):
            if a["href"].startswith("#") or a["href"].startswith("javascript"):
                continue
            link = a
            if len(a.get_text(strip=True)) > 8:
                break
        if link is None:
            continue
        url = urljoin(page_url, link["href"]).split("?")[0]
        if url in seen_urls:
            continue

        ref_before = price_after(r"Przed obniżką", text)
        ref_lowest = price_after(r"Najniższa cena", text)
        ref = ref_before or ref_lowest
        # bieżąca cena: pierwsza cena w bloku, która nie jest ceną referencyjną ani ratą/rabatem kodu
        clean = re.sub(r"(Przed obniżką|Najniższa cena|Cena bez kodu|RATY)\s*:?[^a-ząćęłńóśźż]*", " ", text)
        # usuń kwoty rabatu typu "-100,00 zł" - to nie jest cena produktu
        clean = re.sub(r"[-–−]\s*" + PRICE_RE, " ", clean)
        cur = first_price(clean)
        if not cur or not ref or ref <= cur:
            continue

        name = link.get("title") or link.get_text(" ", strip=True)
        if len(name) < 5:
            img = card.find("img", alt=True)
            name = img["alt"] if img else url
        seen_urls.add(url)
        products.append(
            {
                "url": url,
                "name": name[:150],
                "price": cur,
                "ref_price": ref,
                "ref_type": "przed obniżką" if ref_before else "najniższa z 30 dni",
                "discount": round((1 - cur / ref) * 100, 1),
            }
        )
    return products


def next_page_url(html: str, page_url: str):
    soup = BeautifulSoup(html, "html.parser")
    a = soup.find("a", rel=lambda v: v and "next" in v) or soup.find(
        "a", string=re.compile(r"Następna|Dalej|›|»")
    )
    if a and a.get("href"):
        return urljoin(page_url, a["href"])
    link = soup.find("link", rel="next")
    if link and link.get("href"):
        return urljoin(page_url, link["href"])
    return None


def fetch(session: requests.Session, url: str):
    for attempt in range(3):
        try:
            r = session.get(url, headers=HEADERS, timeout=30)
            if r.status_code == 200:
                return r.text
            print(f"  HTTP {r.status_code} dla {url}", file=sys.stderr)
            if r.status_code in (403, 429):
                time.sleep(5 * (attempt + 1))
            else:
                return None
        except requests.RequestException as e:
            print(f"  błąd sieci: {e}", file=sys.stderr)
            time.sleep(3)
    return None


def load_urls():
    if not URLS_FILE.exists():
        sys.exit("Brak pliku urls.txt - dodaj adresy kategorii/stron promocji, po jednym w linii.")
    return [
        l.strip()
        for l in URLS_FILE.read_text(encoding="utf-8").splitlines()
        if l.strip() and not l.startswith("#")
    ]


def telegram_send(text: str):
    token, chat = os.environ["TELEGRAM_BOT_TOKEN"], os.environ["TELEGRAM_CHAT_ID"]
    r = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat, "text": text, "disable_web_page_preview": True},
        timeout=30,
    )
    r.raise_for_status()


def notify(items):
    items = sorted(items, key=lambda p: -p["discount"])
    chunk = ""
    for p in items:
        line = (
            f"🔥 -{p['discount']:.0f}%  {p['name']}\n"
            f"{p['price']:.2f} zł (było {p['ref_price']:.2f} zł, {p['ref_type']})\n{p['url']}\n\n"
        )
        if len(chunk) + len(line) > 3800:
            telegram_send(chunk)
            chunk = ""
        chunk += line
    if chunk:
        telegram_send(chunk)


def main():
    if "--selftest" in sys.argv:
        return selftest()
    dump = "--dump" in sys.argv
    session = requests.Session()
    state = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}
    all_products = {}

    for start in load_urls():
        print(f"== {start}")
        url, pages = start, 0
        while url and pages < MAX_PAGES:
            html = fetch(session, url)
            if html is None:
                break
            if dump and pages == 0:
                DEBUG_DIR.mkdir(exist_ok=True)
                name = re.sub(r"\W+", "_", start)[-60:] + ".html"
                (DEBUG_DIR / name).write_text(html, encoding="utf-8")
            found = parse_products(html, url)
            print(f"  strona {pages + 1}: {len(found)} produktów z ceną referencyjną")
            for p in found:
                all_products[p["url"]] = p
            pages += 1
            nxt = next_page_url(html, url)
            url = nxt if nxt and nxt != url else None
            time.sleep(DELAY)

    print(f"Razem produktów: {len(all_products)}")
    if not all_products:
        print("UWAGA: 0 produktów - strona mogła zablokować zapytania lub zmienił się układ.")
        sys.exit(1)

    hits = [
        p for p in all_products.values() if p["discount"] >= MIN_DISCOUNT and p["price"] >= MIN_PRICE
    ]
    new = [p for p in hits if p["url"] not in state or p["price"] < state[p["url"]]["price"]]
    print(f"Obniżki >= {MIN_DISCOUNT:.0f}%: {len(hits)}, w tym nowe: {len(new)}")

    if new and not dump:
        notify(new)
    for p in hits:
        state[p["url"]] = {"price": p["price"], "discount": p["discount"], "ts": int(time.time())}
    STATE_FILE.parent.mkdir(exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")


def selftest():
    sample = """
    <ul>
     <li><a href="/laptop-gamingowy-acer-p1.bhtml">Laptop gamingowy ACER Nitro</a>
         <span>4 299 zł</span><span>Najniższa cena: 4 499 zł</span><span>Przed obniżką: 10 999 zł</span>
         <span>RATY: 162,47 zł x 40 rat 0%</span></li>
     <li><a href="/lodowka-p2.bhtml">Lodówka turystyczna XYZ</a>
         <span>Z KODEM -100,00 zł</span><span>799 zł</span><span>Najniższa cena: 899 zł</span></li>
    </ul>"""
    res = parse_products(sample, BASE)
    for p in res:
        print(p)
    assert len(res) == 2, res
    assert res[0]["price"] == 4299 and res[0]["ref_price"] == 10999 and res[0]["discount"] == 60.9, res[0]
    assert res[1]["price"] == 799 and res[1]["ref_price"] == 899, res[1]
    print("selftest OK")


if __name__ == "__main__":
    main()
