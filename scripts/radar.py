#!/usr/bin/env python3
"""Bar Radar - %100 Ücretsiz Playwright Google Maps Kazıyıcı (Apify / API Key Yok)"""
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
CFG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
STATE_PATH = ROOT / "state.json"
RESULTS_PATH = ROOT / "docs" / "results.json"

EXCLUDE_KEYWORDS = [
    "cater", "mobil", "eventservice", "event ", "verleih", "foodtruck",
    "burger", "hamburger", "pizza", "pizzeria", "döner", "kebab", "kebap",
    "shisha", "hookah", "nargile", "steakhouse", "sushi", "ramen", "taco",
    "bowling", "cinema", "kino", "hotel", "hostel", "bäckerei", "bakery",
    "imbiss", "schnitzel", "cafe", "kaffee"
]


def log(*a):
    print(*a, flush=True)


def load_json(path, default):
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return default


def save_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def classify_venue(name, category, reviews_list):
    combined = f"{name} {category} {' '.join(reviews_list)}".lower()
    for kw in EXCLUDE_KEYWORDS:
        if kw in combined:
            return False, f"Filtre ({kw.strip()})"
    return True, "Kokteyl Bar / Lounge"


def accept_cookies(page):
    """Google çerez onay ekranı çıkarsa otomatik kabul eder."""
    try:
        for txt in ["Accept all", "Alle akzeptieren", "Tout accepter", "Tümünü kabul et", "Reject all"]:
            btn = page.locator(f"button:has-text('{txt}')").first
            if btn.is_visible(timeout=1500):
                btn.click()
                time.sleep(1)
                return
    except Exception:
        pass


def scrape_city(page, city_query, max_places=25):
    """Google Maps'te şehri aratır ve listedeki mekanların temel bilgilerini toplar."""
    search_q = f"cocktail bar in {city_query}"
    url = f"https://www.google.com/maps/search/{search_q.replace(' ', '+')}?hl=en"
    page.goto(url, timeout=45000)
    accept_cookies(page)

    try:
        page.wait_for_selector('div[role="feed"]', timeout=15000)
        feed = page.locator('div[role="feed"]')
        for _ in range(4):
            feed.evaluate("el => el.scrollBy(0, 2000)")
            time.sleep(1.2)
    except Exception:
        pass

    links = page.locator('a[href*="/maps/place/"]').all()
    places = []
    seen_urls = set()

    for link in links:
        try:
            href = link.get_attribute("href") or ""
            if not href or href in seen_urls:
                continue
            clean_url = href.split("?")[0]
            if clean_url in seen_urls:
                continue
            seen_urls.add(clean_url)

            aria = link.get_attribute("aria-label") or ""
            parent_text = link.locator("..").inner_text(timeout=1000)
            name = aria.strip() or parent_text.split("\n")[0].strip()
            if not name:
                continue

            # Yıldız ve yorum sayısını metinden ayıkla (Örn: "4.7(84)" veya "4.7 stars 84 Reviews")
            rating = 0.0
            reviews_count = 0
            m = re.search(r"([345]\.\d)\s*\(([\d,]+)\)", parent_text)
            if m:
                rating = float(m.group(1))
                reviews_count = int(m.group(2).replace(",", ""))
            else:
                m2 = re.search(r"([345]\.\d)", parent_text)
                if m2:
                    rating = float(m2.group(1))

            places.append({
                "id": clean_url,
                "name": name,
                "url": clean_url,
                "rating": rating,
                "reviews_count": reviews_count,
                "raw_text": parent_text
            })
            if len(places) >= max_places:
                break
        except Exception:
            continue

    return places


def scrape_place_details(page, place_url):
    """Aday mekanın sayfasına girip kategorisini, yorumlarını ve ne kadar yeni olduğunu kontrol eder."""
    page.goto(place_url + "?hl=en", timeout=40000)
    time.sleep(2)

    body_text = page.locator('div[role="main"]').first.inner_text(timeout=8000) if page.locator('div[role="main"]').count() else ""
    lines = [ln.strip() for ln in body_text.split("\n") if ln.strip()]

    # Kategori ve yıldız/yorum bilgisini tamamla
    category = "Cocktail bar"
    for ln in lines[:18]:
        if any(w in ln.lower() for w in ["bar", "lounge", "restaurant", "pub", "club", "cater", "cafe", "bistro", "grill"]):
            if len(ln) < 40 and not re.search(r"\d", ln):
                category = ln
                break

    rating = 0.0
    reviews_count = 0
    m = re.search(r"([345]\.\d)\s*\(?([\d,]+)\s*reviews?\)?", body_text, re.IGNORECASE)
    if m:
        rating = float(m.group(1))
        reviews_count = int(m.group(2).replace(",", ""))

    # Yorumlar sekmesine tıkla ve görünür yorum metinlerini/tarihlerini al
    sample_reviews = []
    has_very_old_review = False
    try:
        rev_tab = page.locator('button[role="tab"]:has-text("Reviews")').first
        if rev_tab.is_visible(timeout=2500):
            rev_tab.click()
            time.sleep(2)

        # Yorumlarda "2 years ago", "3 years ago" vb. var mı kontrol et
        rev_blocks = page.locator('div[data-review-id]').all()
        for rb in rev_blocks[:10]:
            t = rb.inner_text(timeout=1000)
            if re.search(r"\b([2-9]|\d{2})\s+years?\s+ago\b", t, re.IGNORECASE):
                has_very_old_review = True
            clean_lines = [x.strip() for x in t.split("\n") if len(x.strip()) > 25]
            if clean_lines:
                sample_reviews.append(clean_lines[-1][:280])
    except Exception:
        pass

    return {
        "category": category,
        "rating": rating,
        "reviews_count": reviews_count,
        "has_very_old_review": has_very_old_review,
        "reviews": sample_reviews[:8]
    }


def main():
    now = datetime.now(timezone.utc)
    state = load_json(STATE_PATH, {"city_idx": 0, "seen": {}})
    results = load_json(RESULTS_PATH, [])
    cities = CFG["cities"]

    # Her çalışmada 5 şehir tara (hızlı ve dengeli)
    cities_per_run = min(CFG.get("cities_per_run", 6), 6)
    start = state["city_idx"] % len(cities)
    batch = [cities[(start + i) % len(cities)] for i in range(cities_per_run)]
    state["city_idx"] = (start + len(batch)) % len(cities)
    log("Bu hafta taranan şehirler:", ", ".join(c["name"] for c in batch))

    min_rating = CFG.get("min_rating", 4.0)
    min_rev = CFG.get("min_reviews", 12)
    # Yeni mekanları yakalamak için yorum üst sınırını 160'ta tutuyoruz (eski dev mekanları eler)
    max_rev = min(CFG.get("max_reviews_count", 160), 180)

    candidates = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            locale="en-US"
        )
        page = context.new_page()

        for c in batch:
            try:
                found = scrape_city(page, c["query"], max_places=25)
                city_cands = 0
                for p in found:
                    pid = p["id"]
                    if pid in state["seen"]:
                        continue
                    # Eğer listede puan/yorum okunduysa hızlı filtre uygula
                    if p["rating"] > 0 and p["rating"] < min_rating:
                        continue
                    if p["reviews_count"] > max_rev or (0 < p["reviews_count"] < min_rev):
                        continue
                    p["_city"] = c["name"]
                    candidates.append(p)
                    city_cands += 1
                log(f"  {c['name']}: {len(found)} mekan bulundu, {city_cands} aday seçildi")
            except Exception as e:
                log(f"  {c['name']} atlandı: {e}")

        # Az yorumlular (en yeni olma ihtimali yüksek olanlar) öncelikli, en fazla 12 detay incele
        candidates.sort(key=lambda x: x["reviews_count"] if x["reviews_count"] > 0 else 50)
        candidates = candidates[:12]

        added = 0
        for p in candidates:
            pid = p["id"]
            try:
                det = scrape_place_details(page, p["url"])
                rating = p["rating"] or det["rating"]
                rev_cnt = p["reviews_count"] or det["reviews_count"]

                if rating < min_rating or rev_cnt < min_rev or rev_cnt > max_rev:
                    state["seen"][pid] = {"status": "filtered_out"}
                    log(f"  {p['name']}: ELENDİ (Puan: {rating}, Yorum: {rev_cnt})")
                    continue

                if det["has_very_old_review"]:
                    state["seen"][pid] = {"status": "old"}
                    log(f"  {p['name']}: ELENDİ (Eski yorum tespit edildi)")
                    continue

                cat = det["category"]
                revs = det["reviews"]
                is_bar, reason = classify_venue(p["name"], cat, revs)

                results.append({
                    "id": pid,
                    "name": p["name"],
                    "city": p["_city"],
                    "category": cat,
                    "is_real_bar": is_bar,
                    "filter_reason": reason,
                    "rating": rating,
                    "reviews_count": rev_cnt,
                    "first_review": f"Yeni/Popüler ({rev_cnt} yorum)",
                    "reviews": revs,
                    "summary": f"Kategori: {cat} • {reason}. ({rating}★ / {rev_cnt} yorum).",
                    "maps_url": p["url"],
                    "added": now.date().isoformat()
                })
                state["seen"][pid] = {"status": "ok"}
                added += 1
                log(f"  {p['name']} ({cat}): EKLENDİ -> {'ONAYLI BAR' if is_bar else 'HAM LİSTE'}")
            except Exception as e:
                log(f"  {p['name']} detay hatası: {e}")

        browser.close()

    results.sort(key=lambda r: (r["added"], r.get("rating") or 0), reverse=True)
    save_json(RESULTS_PATH, results)
    save_json(STATE_PATH, state)
    log(f"Bitti! Bu turda {added} yeni mekan eklendi, toplam {len(results)}.")


if __name__ == "__main__":
    main()
    
